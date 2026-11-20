"""Research, parallel focused reviews and one final edit; stop before audio."""

from pathlib import Path
from time import monotonic
import re
from typing import Callable

from neuralcast.config import ASSETS_ROOT
from neuralcast.pipelines.host_orchestrator.speech import (
    INLINE_VOCAL_TAGS,
    validate_speech_transcript,
)
from neuralcast.services import editorial_agy as agy
from .editorial_models import (
    Draft,
    Edit,
    EditorialBrief,
    FactCheck,
    ResearchDossier,
    Review,
)
from .delivery import apply_edit, edit_blockers, edit_report
from .library import resolve_recording
from .manuscript import manuscript_hash, validate_manuscript
from .models import Commentary, Music, Plan, Proposal
from .rendering import HOST_LEAD_IN_SECONDS, audio_duration
from .service import channel_for
from .storage import Project, digest, read_json, write_json, write_text
from .publication import episode_directory
from .reviews import ROLES, examples, finding_ledger

PROMPTS = ASSETS_ROOT / "stories" / "prompts" / "episode_editorial"


def job_settings() -> dict:
    return {
        "research": {
            "provider": "agy",
            "model": agy.model_for("high"),
            "effort": "high",
            "search": True,
            "timeout_seconds": 600,
        },
        "write": {
            "provider": "agy",
            "model": agy.model_for("medium"),
            "effort": "medium",
            "search": False,
            "timeout_seconds": 600,
        },
        "editorial_review": {
            "provider": "agy",
            "model": agy.model_for("medium"),
            "effort": "medium",
            "search": False,
            "timeout_seconds": 420,
        },
        "edit": {
            "provider": "agy",
            "model": agy.model_for("medium"),
            "effort": "medium",
            "search": False,
            "timeout_seconds": 900,
        },
        "fact_check": {
            "provider": "agy",
            "model": agy.model_for("low"),
            "effort": "low",
            "search": False,
            "timeout_seconds": 180,
        },
    }


def _stage_prompt(name: str) -> str:
    filename = "draft.md" if name == "write" else f"{name}.md"
    return (PROMPTS / filename).read_text(encoding="utf-8")


def workshop_inputs(project: Project, *, common: dict | None = None) -> dict:
    if common is None:
        _, _, common = _context(project, "")
    jobs = job_settings()
    prompts = {name: _stage_prompt(name) for name in jobs}
    all_models = {
        "research": ResearchDossier,
        "write": Draft,
        **{role: Review for role in ROLES},
        "edit": Edit,
        "fact_check": FactCheck,
    }
    return {
        "context": common,
        "prompts": prompts,
        "jobs": jobs,
        "schemas": {name: agy.output_schema(all_models[name]) for name in jobs},
    }


def validate_plan(
    plan: Plan, proposal: Proposal, brief: EditorialBrief, recordings: dict
) -> dict:
    if plan.proposal_hash != brief.proposal_hash:
        raise ValueError("Manuscript references a different approved proposal")
    words = validate_manuscript(plan)
    brief.validate_selection(plan)
    selected = []
    tags = 0
    for part in plan.segments:
        if isinstance(part, Music):
            selected.append(part.id)
        elif isinstance(part, Commentary):
            tags += len(validate_speech_transcript(part.script).tags)
            if part.role == "bridge" and not part.sources:
                raise ValueError(f"Factual evidence required for bridge {part.id}")
    if len(selected) != len(set(selected)):
        raise ValueError("Do not repeat a selected recording")
    music_seconds = sum(recordings[key]["duration_seconds"] for key in selected)
    speech_seconds = words * 60 / brief.narration_words_per_minute + tags * 0.5
    silence_seconds = (
        sum(isinstance(part, Commentary) for part in plan.segments)
        * HOST_LEAD_IN_SECONDS
    )
    estimate = music_seconds + speech_seconds + silence_seconds
    return {
        "spoken_words": words,
        "music_seconds": music_seconds,
        "estimated_narration_seconds": speech_seconds,
        "estimated_silence_seconds": silence_seconds,
        "estimated_total_seconds": estimate,
        "estimate_only": True,
    }


def reading_markdown(
    proposal: Proposal,
    plan: Plan,
    edit: Edit,
    timing: dict,
    approval_hash: str,
    blockers: list[str],
    episode_number: int | None = None,
) -> str:
    def escape(text: str) -> str:
        return text.replace("<", "\\<").replace(">", "\\>")

    def duration(seconds: float) -> str:
        total = round(seconds)
        return f"{total // 60}:{total % 60:02d}"

    lines = [
        f"# {escape(proposal.title)}",
        "",
        *([f"Episode: {episode_number}", ""] if episode_number is not None else []),
        (
            "Status: blocked."
            if blockers
            else "Status: awaiting owner manuscript approval."
        ),
        "No audio has been generated by the editorial workflow.",
        "",
        f"Manuscript SHA256: `{approval_hash}`",
        "",
        f"Estimated duration: **{duration(timing['estimated_total_seconds'])}**; "
        f"music {duration(timing['music_seconds'])}; "
        f"narration {duration(timing['estimated_narration_seconds'])} "
        f"({timing['spoken_words']} words). Actual timings follow synthesis.",
        "",
        "## Song list",
        "",
        "| Order | Artist | Song |",
        "| --- | --- | --- |",
    ]
    songs = [part for part in plan.segments if isinstance(part, Music)]
    for index, song in enumerate(songs, 1):
        artist = escape(song.artist).replace("|", "\\|")
        title = escape(song.title).replace("|", "\\|")
        lines.append(f"| {index} | {artist} | {title} |")
    lines += [
        "",
        "## Full manuscript",
        "",
        "Vocal tags are directions, not spoken words.",
        "",
    ]
    lines += [
        f"Each host passage is one TTS generation, preceded by {HOST_LEAD_IN_SECONDS:g}s of silence "
        f"({timing['estimated_silence_seconds']:g}s total, included in the episode estimate).",
        "",
    ]
    for part in plan.segments:
        if isinstance(part, Commentary):
            lines += [
                f"### Voice — {escape(part.companion.title)}",
                "",
                escape(part.script),
                "",
            ]
        else:
            lines += [
                f"### Song — {escape(part.artist)} — {escape(part.title)}",
                "",
                "*Complete recording*",
                "",
            ]
    lines += ["## Editorial changes", ""]
    lines.extend(f"- {escape(passage.explanation)}" for passage in edit.passages)
    lines += ["", "## Production checks", ""]
    lines.extend(f"- {escape(note)}" for note in edit.production_notes)
    if not edit.production_notes:
        lines.append("None reported.")
    lines += ["", "## Listening notes", ""]
    lines.extend(
        f"- {note.segment_id}: {escape(note.note)}" for note in edit.listening_notes
    )
    lines += ["", "## Unresolved issues", ""]
    lines.extend(f"- {escape(item)}" for item in blockers)
    if not blockers:
        lines += ["None reported. Owner review is still required."]
    return "\n".join(lines) + "\n"


def _context(project: Project, feedback: str) -> tuple[Proposal, EditorialBrief, dict]:
    if (episode_directory(project.station, project.id) / "manifest.json").exists():
        raise ValueError(
            "Published episodes cannot be rewritten by editorial or delivery passes"
        )
    proposal, _ = project.approved_proposal()
    brief = project.editorial_brief()
    channel = channel_for(project, proposal)
    recordings = {}
    for song in brief.candidate_songs:
        resolved = resolve_recording(project.station_root, song)
        recordings[song.id] = {
            **{key: value for key, value in resolved.items() if key != "path"},
            "duration_seconds": audio_duration(Path(resolved["path"])),
        }
    return (
        proposal,
        brief,
        {
            "proposal": proposal.model_dump(),
            "proposal_hash": brief.proposal_hash,
            "approved_brief": brief.model_dump(),
            "concept_hash": project.concept_hash(),
            "host": {
                "brand": channel.brand.key,
                "locale": channel.locale.tag,
                "personality": channel.script_style_override
                or channel.brand.script_style,
                "language_guidance": channel.locale.script_guidance,
            },
            "recordings": recordings,
            "writing_references": {
                "writing_guide.md": (PROMPTS / "writing_guide.md").read_text(
                    encoding="utf-8"
                ),
                "voice_examples": examples(
                    "natural-aside",
                    "connected-refinement",
                    "detour-and-return",
                    "explain-the-problem",
                    "attribution-in-speech",
                    "orient-insider-term",
                ),
            },
            "allowed_inline_tags": sorted(INLINE_VOCAL_TAGS),
            "timing_policy": {
                "estimate_only": True,
                "target_seconds": proposal.target_seconds,
                "commentary_budget_seconds": proposal.commentary_budget_seconds,
                "narration_words_per_minute": brief.narration_words_per_minute,
                "narration_estimated_words": int(
                    proposal.commentary_budget_seconds
                    * brief.narration_words_per_minute
                    / 60
                ),
                "tag_allowance_seconds": 0.5,
                "host_lead_in_seconds": HOST_LEAD_IN_SECONDS,
            },
            "owner_feedback": feedback,
        },
    )


def _check_text(value) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _check_text(item)
    elif isinstance(value, list):
        for item in value:
            _check_text(item)
    elif isinstance(value, str) and re.search(r"\b(\w+)(?:\s+\1){11,}\b", value, re.I):
        raise ValueError("Response contains a repeated-token corruption")


def _job(
    directory: Path,
    name: str,
    context: dict,
    model,
    settings: dict,
    prompt: str,
    runner: Callable,
    validate: Callable,
    progress: Callable,
    schema: dict | None = None,
) -> Draft | Review | Edit | ResearchDossier | FactCheck:
    result_path = directory / f"{name}.json"
    if result_path.exists():
        saved = read_json(result_path)
        if (
            not isinstance(saved.get("output"), dict)
            or saved.get("input_hash") != digest(context)
            or saved.get("output_hash") != digest(saved["output"])
        ):
            raise ValueError(f"Stored {name} stage changed")
        _check_text(saved["output"])
        result = model.model_validate(saved["output"])
        validate(result)
        progress(f"Reusing editorial job: {name}")
        return result
    attempts = directory / name
    attempts.mkdir(exist_ok=True)
    request = context
    for retry in range(2):
        attempt = attempts / f"attempt-{len(list(attempts.iterdir())) + 1:03d}"
        raw = None
        started = monotonic()
        progress(
            f"Running editorial job: {name}" + (" (targeted retry)" if retry else "")
        )
        try:
            raw = runner(
                prompt,
                request,
                schema or agy.output_schema(model),
                attempt,
                **{
                    key: value
                    for key, value in settings.items()
                    if key != "provider"
                },
            )
            _check_text(raw)
            result = model.model_validate(raw)
            validate(result)
        except (ValueError, RuntimeError, OSError) as exc:
            failure = attempt / "failure.json"
            if not failure.exists():
                write_json(
                    failure,
                    {
                        "category": (
                            "validation" if isinstance(exc, ValueError) else "execution"
                        ),
                        "status": "failed",
                    },
                )
            category = read_json(failure).get("category")
            if retry or category in {
                "authentication",
                "configuration",
                "model_access",
                "schema",
                "incomplete",
            }:
                raise
            if raw is not None:
                request = {
                    **context,
                    "retry_feedback": {
                        "validation_error": str(exc)[:1000],
                        "previous_response": raw,
                    },
                }
            else:
                request = context
            continue
        finally:
            if not (attempt / "metrics.json").exists():
                write_json(
                    attempt / "metrics.json",
                    {
                        "provider": settings["provider"],
                        "model": settings["model"],
                        "elapsed_seconds": round(monotonic() - started, 3),
                        "usage": None,
                    },
                )
        write_json(
            result_path,
            {
                "input_hash": digest(context),
                "output_hash": digest(raw),
                "output": raw,
                "attempt": str(attempt),
            },
        )
        progress(f"Completed editorial job: {name}")
        return result


def _snapshot(project: Project, directory: Path, inputs: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "inputs.json"
    if path.exists():
        if read_json(path) != inputs:
            raise ValueError("Stored editorial inputs changed")
        return
    current = project.documents / "plan.json"
    manuscript = project.documents / "manuscript.md"
    write_json(
        directory / "starting_plan.json",
        {
            "hash": digest(read_json(current)) if current.exists() else None,
            "manuscript_hash": (
                digest(manuscript.read_text(encoding="utf-8"))
                if manuscript.exists()
                else None
            ),
        },
    )
    if current.exists():
        write_json(directory / "previous_plan.json", read_json(current))
    if manuscript.exists():
        write_text(
            directory / "previous_manuscript.md", manuscript.read_text(encoding="utf-8")
        )
    latest = project.runtime / "editorial_latest.json"
    if latest.exists():
        write_json(directory / "previous_editorial_latest.json", read_json(latest))
    approval = project.runtime / "manuscript_approval.json"
    if approval.exists():
        write_json(directory / "previous_manuscript_approval.json", read_json(approval))
    write_json(path, inputs)


def _install(
    project: Project,
    proposal: Proposal,
    common: dict,
    plan: Plan,
    edit: Edit,
    timing: dict,
    blockers: list[str],
    directory: Path,
    source: Plan,
    findings: list[dict],
    *,
    fact_check_findings: list[dict] = (),
    grounding_warnings: list[str] = (),
) -> dict:
    if project.concept_hash() != common["concept_hash"]:
        raise ValueError("Approved concept changed during editorial production")
    project.approved_proposal()
    current = project.documents / "plan.json"
    starting = read_json(directory / "starting_plan.json")
    final_plan = plan.model_dump()
    current_hash = digest(read_json(current)) if current.exists() else None
    if current_hash not in {starting["hash"], digest(final_plan)}:
        raise ValueError(
            "Plan changed during editorial production; existing edits were preserved"
        )
    approval_hash = manuscript_hash(project, plan)
    markdown = reading_markdown(
        proposal, plan, edit, timing, approval_hash, blockers, project.episode_number
    )
    manuscript = project.documents / "manuscript.md"
    current_markdown_hash = (
        digest(manuscript.read_text(encoding="utf-8")) if manuscript.exists() else None
    )
    if current_markdown_hash not in {starting["manuscript_hash"], digest(markdown)}:
        raise ValueError(
            "Manuscript changed during editorial production; existing edits were preserved"
        )
    receipt = {
        "run_hash": directory.name,
        "concept_hash": common["concept_hash"],
        "manuscript_hash": approval_hash,
        "status": "blocked" if blockers else "awaiting_manuscript_approval",
        "blockers": blockers,
        "timing": timing,
        "manuscript": str(manuscript),
        "artifacts": str(directory),
        "report": str(directory / "edit_report.md"),
        "listening_notes": [note.model_dump() for note in edit.listening_notes],
        "production_notes": edit.production_notes,
        "installed_plan_hash": digest(final_plan),
        "mode": "delivery" if directory.parent.name == "delivery" else "editorial",
        "jobs": {
            str(path.relative_to(directory).parent): read_json(path)
            for path in sorted(directory.glob("*/attempt-*/metrics.json"))
        },
    }
    if fact_check_findings:
        receipt["fact_check_findings"] = list(fact_check_findings)
    if grounding_warnings:
        receipt["grounding_warnings"] = list(grounding_warnings)
    write_text(
        directory / "edit_report.md",
        edit_report(source, plan, edit, findings, fact_check_findings),
    )
    write_json(directory / "final_plan.json", final_plan)
    write_text(directory / "manuscript.md", markdown)
    write_json(directory / "receipt.json", receipt)
    write_json(current, final_plan)
    write_text(manuscript, markdown)
    write_json(project.runtime / "editorial_latest.json", receipt)
    return receipt


def _edit_context(
    common: dict,
    source: Plan,
    timing: dict,
    findings: list[dict],
    evidence: list[dict],
    strengths: dict,
    production_notes: list[str],
    *,
    script_only: bool = False,
) -> dict:
    return {
        **common,
        "source_plan": source.model_dump(),
        "estimated_duration": timing,
        "findings": findings,
        "additional_sources": evidence,
        "strengths": strengths,
        "production_notes": production_notes,
        "locked_metadata": script_only,
    }


def _final_edit(
    project: Project,
    common: dict,
    proposal: Proposal,
    brief: EditorialBrief,
    directory: Path,
    context: dict,
    prompt: str,
    settings: dict,
    runner: Callable,
    progress: Callable,
    schema: dict | None = None,
    *,
    fact_check_settings: dict | None = None,
    fact_check_prompt: str | None = None,
    fact_check_schema: dict | None = None,
) -> dict:
    source = Plan.model_validate(context["source_plan"])

    def validate(edit):
        edit_blockers(edit, context["findings"])
        plan = apply_edit(
            source,
            edit,
            script_only=context["locked_metadata"],
            evidence=[
                *brief.model_dump()["initial_research"],
                *context["additional_sources"],
            ],
        )
        validate_plan(plan, proposal, brief, common["recordings"])

    edit = _job(
        directory,
        "edit",
        context,
        Edit,
        settings,
        prompt,
        runner,
        validate,
        progress,
        schema=schema,
    )
    edit.production_notes = list(
        dict.fromkeys([*context["production_notes"], *edit.production_notes])
    )
    plan = apply_edit(
        source,
        edit,
        script_only=context["locked_metadata"],
        evidence=[
            *brief.model_dump()["initial_research"],
            *context["additional_sources"],
        ],
    )
    blockers = edit_blockers(edit, context["findings"])
    fact_check_findings: list[dict] = []
    if fact_check_settings and fact_check_prompt:
        ids = {part.id for part in plan.segments}

        def validate_fact_check(result: FactCheck) -> None:
            if any(set(finding.segment_ids) - ids for finding in result.findings):
                raise ValueError("Fact check refers to unknown manuscript segment IDs")

        def run_fact_check(stage_name: str, candidate: Plan) -> FactCheck:
            check_context = {
                "proposal": common["proposal"],
                "approved_brief": common["approved_brief"],
                "source_plan": source.model_dump(),
                "findings": context["findings"],
                "additional_sources": context["additional_sources"],
                "final_plan": candidate.model_dump(),
            }
            return _job(
                directory,
                stage_name,
                check_context,
                FactCheck,
                fact_check_settings,
                fact_check_prompt,
                runner,
                validate_fact_check,
                progress,
                schema=fact_check_schema,
            )

        checked = run_fact_check("fact_check", plan)
        major_drift = [
            {
                "id": f"fact_check-{index}",
                "reviewer": "editorial",
                **item.model_dump(),
            }
            for index, item in enumerate(checked.findings, 1)
            if item.severity == "major"
        ]
        if major_drift:
            repair_context = _edit_context(
                common,
                plan,
                validate_plan(plan, proposal, brief, common["recordings"]),
                major_drift,
                context["additional_sources"],
                {},
                edit.production_notes,
                script_only=context["locked_metadata"],
            )

            def validate_repair(repaired_edit):
                edit_blockers(repaired_edit, major_drift)
                repaired_plan = apply_edit(
                    plan,
                    repaired_edit,
                    script_only=context["locked_metadata"],
                    evidence=[
                        *brief.model_dump()["initial_research"],
                        *context["additional_sources"],
                    ],
                )
                validate_plan(repaired_plan, proposal, brief, common["recordings"])

            repaired = _job(
                directory,
                "fact_check_repair",
                repair_context,
                Edit,
                settings,
                prompt,
                runner,
                validate_repair,
                progress,
                schema=schema,
            )
            edit.production_notes = list(
                dict.fromkeys([*edit.production_notes, *repaired.production_notes])
            )
            plan = apply_edit(
                plan,
                repaired,
                script_only=context["locked_metadata"],
                evidence=[
                    *brief.model_dump()["initial_research"],
                    *context["additional_sources"],
                ],
            )
            blockers = list(
                dict.fromkeys([*blockers, *edit_blockers(repaired, major_drift)])
            )
            checked = run_fact_check("fact_check_recheck", plan)

        fact_check_findings = [item.model_dump() for item in checked.findings]
        blockers = list(
            dict.fromkeys(
                [
                    *blockers,
                    *[
                        f"fact_check-{index}: {item.problem}"
                        for index, item in enumerate(checked.findings, 1)
                        if item.severity == "major"
                    ],
                ]
            )
        )
    return _install(
        project,
        proposal,
        common,
        plan,
        edit,
        validate_plan(plan, proposal, brief, common["recordings"]),
        blockers,
        directory,
        source,
        context["findings"],
        fact_check_findings=fact_check_findings,
    )


def edit_episode(
    project: Project,
    *,
    feedback: str = "",
    runner: Callable = agy.run_stage,
    progress: Callable[[str], None] = lambda _: None,
    frozen_inputs: dict | None = None,
) -> dict:
    """Caller holds project.lock(); accepted jobs survive independent failures."""
    if frozen_inputs is None:
        proposal, brief, common = _context(project, feedback)
    else:
        proposal, _ = project.approved_proposal()
        brief = project.editorial_brief()
        common = frozen_inputs["context"]
    inputs = frozen_inputs or workshop_inputs(project, common=common)
    common = inputs["context"]
    if common["concept_hash"] != project.concept_hash():
        raise ValueError("Frozen editorial concept changed")
    jobs, prompts = inputs["jobs"], inputs["prompts"]
    directory = project.runtime / "editorial" / digest(inputs)
    _snapshot(project, directory, inputs)
    initial_urls = {source.url for source in brief.initial_research}
    research_context = {k: v for k, v in common.items() if k != "writing_references"}
    dossier = _job(
        directory,
        "research",
        research_context,
        ResearchDossier,
        jobs["research"],
        prompts["research"],
        runner,
        lambda _result: None,
        progress,
        schema=inputs["schemas"]["research"],
    )
    write_context = {**common, "research_dossier": dossier.model_dump()}

    def validate_draft(result: Draft) -> None:
        validate_plan(result.plan, proposal, brief, common["recordings"])
        allowed = initial_urls | {source.url for source in dossier.verified_sources}
        cited = {
            source.url
            for part in result.plan.segments
            if isinstance(part, Commentary)
            for source in part.sources
        }
        if cited - allowed:
            raise ValueError(
                "Draft introduced a source outside the verified research dossier"
            )

    draft = _job(
        directory,
        "write",
        write_context,
        Draft,
        jobs["write"],
        prompts["write"],
        runner,
        validate_draft,
        progress,
        schema=inputs["schemas"]["write"],
    )
    draft.production_notes = list(
        dict.fromkeys([*dossier.production_notes, *draft.production_notes])
    )
    timing = validate_plan(draft.plan, proposal, brief, common["recordings"])
    review_context = {
        **common,
        "source_plan": draft.plan.model_dump(),
        "research_notes": draft.research_notes,
        "open_questions": draft.open_questions,
        "estimated_duration": timing,
    }

    def validate_review(result: Review) -> None:
        ids = {part.id for part in draft.plan.segments}
        if any(set(finding.segment_ids) - ids for finding in result.findings):
            raise ValueError("Review refers to unknown manuscript segment IDs")
        if result.additional_sources:
            raise ValueError("Text-only reviewers cannot add research sources")

    review_report = _job(
        directory,
        "editorial_review",
        {**review_context, "assignment": "editorial_review"},
        Review,
        jobs["editorial_review"],
        prompts["editorial_review"],
        runner,
        validate_review,
        progress,
        schema=inputs["schemas"]["editorial_review"],
    )
    reports = {"editorial_review": review_report}
    findings = finding_ledger(draft, reports)
    write_json(directory / "findings.json", {"findings": findings})
    context = _edit_context(
        common,
        draft.plan,
        timing,
        findings,
        [source.model_dump() for source in dossier.verified_sources],
        {"editorial_review": review_report.strengths},
        draft.production_notes,
    )
    return _final_edit(
        project,
        common,
        proposal,
        brief,
        directory,
        context,
        prompts["edit"],
        jobs["edit"],
        runner,
        progress,
        schema=inputs["schemas"]["edit"],
        fact_check_settings=jobs.get("fact_check"),
        fact_check_prompt=prompts.get("fact_check"),
        fact_check_schema=inputs["schemas"].get("fact_check"),
    )


def polish_delivery(
    project: Project,
    *,
    feedback: str = "",
    runner: Callable = agy.run_stage,
    progress: Callable[[str], None] = lambda _: None,
) -> dict:
    """Caller holds project.lock(); reuse evidence for one tool-free narration edit."""
    from .revision import revise_manuscript

    return revise_manuscript(
        project,
        feedback,
        script_only=True,
        runner=runner,
        progress=progress,
    )

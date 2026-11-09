"""Episode planning, saved owner review and resumable admin operations."""

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import fcntl
import uuid
import re
import unicodedata
from typing import Any, Iterator

from pydantic import TypeAdapter
from neuralcast.config import RUNTIME_ROOT, ALLOWED_STATION_SLUGS, station_dir_from_slug
from neuralcast.playlists.catalog import StationPlaylistCatalog
from neuralcast.services import editorial_agent as codex, editorial_text as gemini
from . import editorial
from .editorial_models import EditorialBrief
from .workspace_models import Topics, Concept, Feedback
from .models import Proposal, Plan, Identifier
from .storage import Project, read_json, write_json, digest, file_hash
from .library import resolve_recording
from .rendering import audio_duration, HOST_LEAD_IN_SECONDS
from .manuscript import manuscript_hash, approve_manuscript
from .publication import episode_directory, publish, set_episode_number
from .service import prepare
from .revision import revise_manuscript


class ConflictError(RuntimeError):
    """The requested review changed or another operation owns its lock."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Workspace:
    def __init__(
        self,
        station: str,
        *,
        station_root: Path | None = None,
        runtime_root: Path | None = None,
    ) -> None:
        if station not in ALLOWED_STATION_SLUGS:
            raise ValueError("Unknown station")
        self.station = station
        self.station_root = station_root or station_dir_from_slug(station)
        self.runtime_root = runtime_root or RUNTIME_ROOT / "episodes"
        self.root = self.runtime_root / station

    def project(self, episode_id: str) -> Project:
        return Project(
            self.station,
            episode_id,
            station_root=self.station_root,
            runtime_root=self.runtime_root,
        )

    @contextmanager
    def lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "workspace.lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ConflictError(
                    "Episode workspace is busy; retry shortly"
                ) from None
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def state(self) -> dict[str, Any]:
        path = self.root / "workspace.json"
        return (
            read_json(path)
            if path.exists()
            else {
                "ideas": [],
                "weekly_enabled": False,
                "target_minutes": 45,
                "ideas_week": None,
                "manuscript_week": None,
                "managed_episodes": [],
            }
        )

    def projects(self) -> list[Project]:
        root = self.station_root / "metadata" / "episodes"
        return [
            self.project(read_json(p)["episode_id"])
            for p in sorted(
                root.glob("*/proposal.json"),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
        ]

    def revision(self, project: Project) -> str:
        paths = {
            "proposal": project.documents / "proposal.json",
            "plan": project.documents / "plan.json",
            "feedback": project.runtime / "review.json",
            "editorial": project.runtime / "editorial_latest.json",
            "audio": project.runtime / "latest.json",
            "approval": project.runtime / "proposal_approval.json",
        }
        return digest(
            {k: read_json(path) if path.exists() else None for k, path in paths.items()}
        )

    def check(self, project: Project, revision: str) -> None:
        if self.revision(project) != revision:
            raise ConflictError("This episode changed. Reload before submitting.")

    def detail(self, project: Project) -> dict[str, Any]:
        proposal = project.proposal()
        plan_path = project.documents / "plan.json"
        plan = Plan.model_validate(read_json(plan_path)) if plan_path.exists() else None
        editorial_path = project.runtime / "editorial_latest.json"
        report = read_json(editorial_path) if editorial_path.exists() else {}
        approval = project.runtime / "proposal_approval.json"
        approved = (
            approval.exists()
            and read_json(approval).get("concept_hash") == project.concept_hash()
        )
        publication = episode_directory(project.station, project.id) / "manifest.json"
        latest = project.runtime / "latest.json"
        audio = None
        if latest.exists():
            saved = read_json(latest)
            revision = saved["revision"]
            if (
                not isinstance(revision, str)
                or len(revision) != 64
                or any(c not in "0123456789abcdef" for c in revision)
            ):
                raise ValueError("Invalid saved audio revision")
            production = read_json(project.runtime / revision / "production.json")
            audio = {
                "revision": revision,
                "render_hash": production["render_hash"],
                "duration": production["duration"],
                "chapters": production["chapters"],
            }
            # An older render is available for comparison, but cannot be approved as current.
            matching = plan is not None and all(
                next(
                    (
                        c.get("transcript")
                        for c in production["chapters"]
                        if c["id"] == s.id
                    ),
                    None,
                )
                == editorial.validate_speech_transcript(s.script).plain_text
                for s in plan.segments
                if s.kind == "commentary"
            )
            audio["current"] = matching and bool(
                (project.runtime / "manuscript_approval.json").exists()
                and read_json(project.runtime / "manuscript_approval.json").get(
                    "manuscript_hash"
                )
                == manuscript_hash(project, plan)
            )
        status = (
            "published"
            if publication.exists()
            else (
                "audio_review"
                if audio and audio["current"]
                else (
                    "manuscript_review"
                    if plan
                    else "queued" if approved else "brief_review"
                )
            )
        )
        feedback_path = project.runtime / "review.json"
        feedback = (
            read_json(feedback_path)
            if feedback_path.exists()
            else {"overall": "", "sections": {}, "research": False}
        )
        changes = []
        if report.get("artifacts"):
            artifact = Path(report["artifacts"])
            if (
                artifact.resolve().is_relative_to(project.runtime.resolve())
                and (artifact / "edit.json").exists()
            ):
                saved_edit = read_json(artifact / "edit.json")
                changes = [
                    {
                        "segment_id": item["segment_id"],
                        "explanation": item["explanation"],
                    }
                    for item in saved_edit.get("output", saved_edit).get("passages", [])
                ]
        history = []
        for parent in ("editorial", "delivery", "revisions"):
            for path in (project.runtime / parent).glob("*/receipt.json"):
                receipt = read_json(path)
                history.append(
                    {
                        "id": path.parent.name,
                        "kind": parent,
                        "manuscript_hash": receipt.get("manuscript_hash"),
                        "created_at": datetime.fromtimestamp(
                            path.stat().st_mtime, timezone.utc
                        ).isoformat(),
                    }
                )
        songs = []
        if proposal.editorial_brief:
            for s in project.editorial_brief().candidate_songs:
                songs.append(s.model_dump())
        return {
            "id": project.id,
            "station": self.station,
            "status": status,
            "revision": self.revision(project),
            "proposal": proposal.model_dump(),
            "brief": proposal.editorial_brief,
            "changes": changes,
            "concept_hash": project.concept_hash(),
            "concept_approved": approved,
            "episode_number": project.episode_number,
            "songs": songs,
            "segments": [s.model_dump() for s in plan.segments] if plan else [],
            "manuscript_hash": manuscript_hash(project, plan) if plan else None,
            "blockers": (
                report.get("blockers", [])
                if plan
                and report.get("manuscript_hash") == manuscript_hash(project, plan)
                else []
            ),
            "timing": report.get("timing"),
            "audio": audio,
            "feedback": feedback,
            "history": sorted(history, key=lambda x: x["created_at"], reverse=True)[
                :20
            ],
        }

    def view(self, episode_id: str | None = None) -> dict[str, Any]:
        state = self.state()
        episodes = []
        for p in self.projects():
            d = self.detail(p)
            episodes.append(
                {k: d[k] for k in ("id", "status", "episode_number", "revision")}
                | {
                    "title": d["proposal"]["title"],
                    "managed": p.id in state.get("managed_episodes", []),
                }
            )
        return {
            "station": self.station,
            "revision": digest(state),
            **state,
            "episodes": episodes,
            "episode": self.detail(self.project(episode_id)) if episode_id else None,
        }

    def save_schedule(
        self, revision: str, enabled: bool, target_minutes: int
    ) -> dict[str, Any]:
        with self.lock():
            state = self.state()
            if digest(state) != revision:
                raise ConflictError("Workspace changed; reload before saving")
            if (
                type(enabled) is not bool
                or type(target_minutes) is not int
                or not 10 <= target_minutes <= 120
            ):
                raise ValueError("Choose a duration from 10 to 120 minutes")
            state.update(weekly_enabled=enabled, target_minutes=target_minutes)
            write_json(self.root / "workspace.json", state)
        return self.view()

    def save_feedback(self, episode_id: str, payload: Feedback) -> dict[str, Any]:
        p = self.project(episode_id)
        with p.lock():
            self.check(p, payload.revision)
            d = self.detail(p)
            if d["status"] == "published":
                raise ValueError("Published episodes are immutable")
            ids = {s["id"] for s in d["segments"] if s["kind"] == "commentary"}
            if set(payload.sections) - ids:
                raise ValueError("Feedback refers to an unknown passage")
            write_json(
                p.runtime / "review.json", payload.model_dump(exclude={"revision"})
            )
        return self.detail(p)

    def submit(
        self,
        action: str,
        *,
        episode_id: str | None = None,
        revision: str | None = None,
        topic: str = "",
        target_minutes: int = 45,
        render_hash: str | None = None,
        description: str = "",
    ) -> str:
        if action not in {
            "ideas",
            "develop",
            "queue",
            "write",
            "revise",
            "render",
            "publish",
        }:
            raise ValueError("Unknown episode action")
        with self.lock():
            if action == "develop" and episode_id:
                p = self.project(episode_id)
                self.check(p, revision)
                if (
                    self.detail(p)["segments"]
                    or self.detail(p)["status"] == "published"
                ):
                    raise ValueError(
                        "Revise the manuscript of an existing episode instead"
                    )
            if action in {"ideas", "develop"}:
                if not episode_id and revision != digest(self.state()):
                    raise ConflictError("Workspace changed; reload before submitting")
                if not 10 <= target_minutes <= 120 or type(target_minutes) is not int:
                    raise ValueError("Invalid requested duration")
                if action == "develop" and not topic.strip():
                    raise ValueError("Describe the topic to develop")
            else:
                if not episode_id:
                    raise ValueError("Choose an episode")
                p = self.project(episode_id)
                self.check(p, revision)
                d = self.detail(p)
                if d["status"] == "published":
                    raise ValueError("Published episodes are immutable")
                if action in {"queue", "write"} and d["segments"]:
                    raise ValueError(
                        "Use manuscript feedback to revise an existing draft"
                    )
                if action in {"revise", "render"} and not d["segments"]:
                    raise ValueError("Generate a manuscript first")
                if action == "revise" and not (
                    d["feedback"]["overall"].strip()
                    or any(v.strip() for v in d["feedback"]["sections"].values())
                ):
                    raise ValueError("Add feedback before requesting a revision")
                if action == "render" and d["blockers"]:
                    raise ValueError(
                        "Resolve the listed editorial questions before rendering"
                    )
                if action == "publish" and (
                    not d["audio"]
                    or not d["audio"]["current"]
                    or render_hash != d["audio"]["render_hash"]
                ):
                    raise ValueError(
                        "Publication must approve the current reviewed audio"
                    )
            request_id = uuid.uuid4().hex
            request = {
                "id": request_id,
                "action": action,
                "episode_id": episode_id,
                "revision": revision,
                "topic": topic,
                "target_minutes": target_minutes,
                "render_hash": render_hash,
                "description": description,
                "created_at": now(),
            }
            if episode_id:
                project = self.project(episode_id)
                request["concept_hash"] = project.concept_hash()
                if (project.documents / "plan.json").exists():
                    request["source_plan"] = read_json(project.documents / "plan.json")
                    request["manuscript_hash"] = manuscript_hash(
                        project, Plan.model_validate(request["source_plan"])
                    )
            if action == "revise":
                request["feedback"] = self.detail(self.project(episode_id))["feedback"]
                report_path = self.project(episode_id).runtime / "editorial_latest.json"
                request["source_editorial"] = (
                    read_json(report_path) if report_path.exists() else {}
                )
            write_json(self.root / "requests" / request_id / "request.json", request)
            return request_id

    def _catalogue(self, topic: str = "") -> list[dict[str, str]]:
        catalog = StationPlaylistCatalog(
            self.station_root / "playlists", log=lambda _: None
        )
        recordings = []
        for path in sorted((self.station_root / "playlists").glob("*.csv")):
            for song in catalog.load(path.stem).songs:
                recordings.append(
                    {"playlist": path.stem, "artist": song.artist, "title": song.title}
                )
        tokens = [t.casefold() for t in topic.split() if len(t) > 3]
        recordings.sort(
            key=lambda s: -sum(
                t in (s["artist"] + " " + s["title"]).casefold() for t in tokens
            )
        )
        # Include a small selection from every playlist alongside topic matches.
        selected = recordings[:250]
        for path in sorted((self.station_root / "playlists").glob("*.csv")):
            selected.extend([s for s in recordings if s["playlist"] == path.stem][:8])
        unique = {tuple(s.values()): s for s in selected}
        return list(unique.values())[:450]

    def _planning_context(self, request: dict[str, Any]) -> dict[str, Any]:
        from neuralcast.pipelines.host_orchestrator.channels import get_channel_registry

        channels = [
            c
            for c in get_channel_registry().channels.values()
            if c.content_station == self.station and c.locale.tag == "es-AR"
        ]
        if len(channels) != 1:
            raise ValueError(
                "The station must have exactly one Spanish podcast channel"
            )
        channel = channels[0]
        return {
            "station": self.station,
            "channel": channel.key,
            "current_brief": (
                read_json(
                    self.project(request["episode_id"]).documents / "proposal.json"
                )
                if request.get("episode_id")
                else None
            ),
            "language": channel.locale.script_guidance,
            "personality": channel.script_style_override or channel.brand.script_style,
            "editorial_definitions": (
                self.station_root / "playlist_descriptions.md"
            ).read_text(),
            "catalogue": self._catalogue(request.get("topic", "")),
            "previous_episodes": [
                {"title": p.proposal().title, "premise": p.proposal().premise}
                for p in self.projects()
            ],
            "owner_topic": request.get("topic", ""),
            "target_seconds": request["target_minutes"] * 60,
            "writing_guide": (editorial.PROMPTS / "writing_guide.md").read_text(),
            "request_id": request["id"],
        }

    def execute(self, request_id: str) -> dict[str, Any]:
        request_id = TypeAdapter(Identifier).validate_python(request_id)
        directory = self.root / "requests" / request_id
        request = read_json(directory / "request.json")
        if (directory / "result.json").exists():
            return read_json(directory / "result.json")
        action = request["action"]
        print(f"Episode workspace: {action}", flush=True)
        if action == "weekly":
            response = self.weekly()
        elif action in {"ideas", "develop"}:
            context = self._planning_context(request)
            model = Topics if action == "ideas" else Concept
            prompt = (
                editorial.PROMPTS / ("topics.md" if action == "ideas" else "concept.md")
            ).read_text()
            settings = (
                dict(
                    provider="gemini",
                    model=gemini.MODEL,
                    thinking_level="low",
                    max_output_tokens=8192,
                    timeout_seconds=300,
                )
                if action == "ideas"
                else dict(
                    provider="codex",
                    model=codex.MODEL,
                    reasoning_effort="medium",
                    timeout_seconds=600,
                )
            )
            generated = []

            def validate(result):
                if action == "develop":
                    EditorialBrief(
                        proposal_hash="0" * 64,
                        instructions=result.instructions,
                        candidate_songs=result.candidate_songs,
                        initial_research=result.initial_research,
                    )
                    measured = []
                    for song in result.candidate_songs:
                        recording = resolve_recording(self.station_root, song)
                        measured.append(audio_duration(Path(recording["path"])))
                    budget = round(
                        context["target_seconds"]
                        - sum(measured)
                        - (len(measured) + 1) * HOST_LEAD_IN_SECONDS
                    )
                    if not 60 <= budget <= 1200:
                        raise ValueError(
                            f"Selected music totals {sum(measured):.0f}s; target {context['target_seconds']}s requires 60-1200s narration. Adjust song selection."
                        )
                    generated[:] = [budget]

            result = editorial._job(
                directory,
                "topics" if action == "ideas" else "concept",
                context,
                model,
                settings,
                prompt,
                gemini.run_stage if action == "ideas" else codex.run_stage,
                validate,
                print,
            )
            with self.lock():
                if action == "ideas":
                    state = self.state()
                    state["ideas"] = [
                        {"id": uuid.uuid4().hex, **idea.model_dump()}
                        for idea in result.ideas
                    ]
                    write_json(self.root / "workspace.json", state)
                    response = {"ideas": state["ideas"]}
                else:
                    slug = (
                        re.sub(
                            r"[^a-z0-9]+",
                            "-",
                            unicodedata.normalize("NFKD", result.title)
                            .encode("ascii", "ignore")
                            .decode()
                            .lower(),
                        )
                        .strip("-")[:65]
                        .rstrip("-")
                        or "episode"
                    )
                    episode_id = request.get("episode_id") or f"{slug}-{request_id[:6]}"
                    p = self.project(episode_id)
                    proposal = Proposal(
                        episode_id=episode_id,
                        channel=context["channel"],
                        title=result.title,
                        premise=result.premise,
                        target_seconds=context["target_seconds"],
                        commentary_budget_seconds=generated[0],
                        outline=result.outline,
                    )
                    brief = EditorialBrief(
                        proposal_hash=digest(proposal.model_dump()),
                        instructions=result.instructions,
                        candidate_songs=result.candidate_songs,
                        initial_research=result.initial_research,
                    )
                    document = {
                        **proposal.model_dump(),
                        "editorial_brief": brief.model_dump(),
                    }
                    with p.lock():
                        if request.get("episode_id"):
                            if read_json(p.documents / "proposal.json") != document:
                                self.check(p, request["revision"])
                                write_json(
                                    directory / "previous_proposal.json",
                                    read_json(p.documents / "proposal.json"),
                                )
                        elif (p.documents / "proposal.json").exists() and read_json(
                            p.documents / "proposal.json"
                        ) != document:
                            raise ConflictError(
                                "Developed brief changed; existing episode was preserved"
                            )
                        write_json(p.documents / "proposal.json", document)
                    state = self.state()
                    state["managed_episodes"] = list(
                        dict.fromkeys([*state.get("managed_episodes", []), episode_id])
                    )
                    write_json(self.root / "workspace.json", state)
                    response = {"episode_id": episode_id}
        else:
            p = self.project(request["episode_id"])
            with p.lock():
                if p.concept_hash() != request["concept_hash"]:
                    raise ConflictError(
                        "The approved concept changed; submit a new request"
                    )
                if (
                    action == "render"
                    and manuscript_hash(
                        p, Plan.model_validate(read_json(p.documents / "plan.json"))
                    )
                    != request["manuscript_hash"]
                ):
                    raise ConflictError(
                        "The manuscript changed; review it before rendering"
                    )
                # Completed owner approvals may survive a failed long-running job.
                marker = directory / "started.json"
                if not marker.exists():
                    self.check(p, request["revision"])
                    write_json(marker, {"started_at": now()})
                if (episode_directory(p.station, p.id) / "manifest.json").exists():
                    if action != "publish":
                        raise ValueError("Published episodes are immutable")
                if action in {"queue", "write"}:
                    p.approve_proposal(request["concept_hash"])
                    response = (
                        {"queued": True}
                        if action == "queue"
                        else editorial.edit_episode(p, progress=print)
                    )
                elif action == "revise":
                    response = revise_manuscript(
                        p,
                        request["feedback"],
                        source_plan=request["source_plan"],
                        source_editorial=request["source_editorial"],
                        progress=print,
                    )
                elif action == "render":
                    plan = Plan.model_validate(read_json(p.documents / "plan.json"))
                    approve_manuscript(p, manuscript_hash(p, plan))
                    from neuralcast.services.azuracast_config import (
                        load_azuracast_settings,
                    )
                    from neuralcast.pipelines.host_orchestrator.transport import (
                        AzuraCastClient,
                    )

                    settings = load_azuracast_settings(station=self.station)
                    response = prepare(
                        p, AzuraCastClient(settings.base_url, settings.api_key)
                    )
                elif action == "publish":
                    if p.episode_number is None:
                        numbers = [q.episode_number or 0 for q in self.projects()]
                        set_episode_number(p, max(numbers, default=0) + 1)
                    response = publish(
                        p, request["render_hash"], request.get("description")
                    )
                    write_json(
                        directory / "publication_approval.json",
                        {
                            "render_hash": request["render_hash"],
                            "approved_at": request["created_at"],
                        },
                    )
                else:
                    raise ValueError("Unknown saved operation")
        if action in {"queue", "write"}:
            with self.lock():
                state = self.state()
                state["managed_episodes"] = list(
                    dict.fromkeys(
                        [*state.get("managed_episodes", []), request["episode_id"]]
                    )
                )
                write_json(self.root / "workspace.json", state)
        write_json(directory / "result.json", response)
        return response

    def preview_audio(self, episode_id: str, revision: str) -> Path:
        p = self.project(episode_id)
        if len(revision) != 64 or any(c not in "0123456789abcdef" for c in revision):
            raise ValueError("Unknown audio revision")
        receipt = read_json(p.runtime / revision / "production.json")
        if (
            receipt["station"] != self.station
            or receipt["episode_id"] != p.id
            or receipt["revision"] != revision
        ):
            raise ValueError("Audio identity mismatch")
        path = p.runtime / revision / "episode.mp3"
        if file_hash(path) != receipt["render_hash"]:
            raise ValueError("Preview audio changed")
        return path

    def weekly(self) -> dict[str, Any]:
        state = self.state()
        if not state["weekly_enabled"]:
            return {"skipped": "Weekly preparation is disabled"}
        week = datetime.now(timezone.utc).strftime("%G-W%V")
        pending = [
            self.detail(p)
            for p in self.projects()
            if p.id in state.get("managed_episodes", [])
        ]
        if any(d["status"] in {"manuscript_review", "audio_review"} for d in pending):
            return {"skipped": "An episode awaits owner review"}
        if state.get("ideas_week") != week:
            request = self.submit(
                "ideas", revision=digest(state), target_minutes=state["target_minutes"]
            )
            self.execute(request)
            with self.lock():
                state = self.state()
                state["ideas_week"] = week
                write_json(self.root / "workspace.json", state)
        queued = next((d for d in pending if d["status"] == "queued"), None)
        if queued and self.state().get("manuscript_week") != week:
            request = self.submit(
                "write", episode_id=queued["id"], revision=queued["revision"]
            )
            result = self.execute(request)
            with self.lock():
                state = self.state()
                state["manuscript_week"] = week
                write_json(self.root / "workspace.json", state)
            return {"episode_id": queued["id"], "status": result.get("status")}
        return {"ideas_ready": True}

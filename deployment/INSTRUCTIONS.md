# Deployment Instructions for Story Injector

This package contains the necessary source code and dependencies to run the `story_injector.py` script on your VPS.

## Prerequisites

- Access to the VPS terminal.

## Step 1: Transfer the package

Transfer the `deploy_story_injector.zip` file to your VPS. Replace `user@your-vps-ip` with your actual username and IP address.

```bash
scp deploy_story_injector.zip user@your-vps-ip:~/deploy_story_injector.zip
```

## Step 2: Set up on the VPS

SSH into your VPS and perform the following steps:

1.  **Install Python and Unzip:**

    Update your package list and install Python 3, pip, venv, and unzip.

    ```bash
    # For Debian/Ubuntu
    sudo apt update
    sudo apt install -y python3 python3-pip python3-venv unzip
    ```

2.  **Unzip the package:**

    ```bash
    unzip deploy_story_injector.zip -d radio_stories
    cd radio_stories
    ```

3.  **Set up a virtual environment and install dependencies:**

    ```bash
    python3 -m venv venv
    source venv/bin/activate
    pip install -r vps_requirements.txt
    ```

4.  **Configure Environment Variables:**

    Create a `.env` file in the `radio_stories` directory:

    ```bash
    nano .env
    ```

    Paste your configuration keys (modify values as needed):

    ```env
    AZURACAST_API_KEY=your_azuracast_key_here
    AZURACAST_BASE_URL=https://your-radio-url.com
    OPENAI_API_KEY=your_openai_key_here
    ```

    *Save and exit (`Ctrl+O`, `Enter`, `Ctrl+X`).*

## Step 3: Test Manually

Before setting up the cronjob, verify that the script runs correctly. The `src` directory needs to be in the python path.

```bash
# Ensure you are inside the radio_stories folder and venv is active
export PYTHONPATH=$PYTHONPATH:$(pwd)/src
python3 src/neuralcast/pipelines/story_injector.py --station neuralcast --dry-run
```

-   Remove `--dry-run` to actually upload and inject a story.
-   Replace `neuralcast` with your station's shortcode if different.

## Step 4: Setup the Cronjob

Open your crontab editor:

```bash
crontab -e
```

Add a line to run the script periodically (e.g., every hour).
**Note:** We set `PYTHONPATH` inline to ensure the imports work correctly.

```cron
# Run every hour at the 5th minute
5 * * * * cd /home/user/radio_stories && export PYTHONPATH=$PYTHONPATH:$(pwd)/src && ./venv/bin/python3 src/neuralcast/pipelines/story_injector.py --station neuralcast >> /home/user/radio_stories/story.log 2>&1
```

### Common Flags

-   `--station <shortcode>`: Target station (default: `neuralcast`).
-   `--min-listeners <n>`: Minimum listeners required to run (default: `1`).
-   `--dry-run`: Generate files locally but do not upload to AzuraCast.

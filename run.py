import feedparser
import requests
import schedule
import time
from datetime import datetime, timedelta
import json
import os

# Configuration
RSS_FEEDS = [
    '',
    '',
    ''
]
QB_SERVER = ''  # Change if your qBittorrent web UI is hosted elsewhere
QB_USERNAME = ''
QB_PASSWORD = ''
DISCORD_WEBHOOK_URL = ''
INTERVAL_DAYS = 60
TORRENT_RETENTION_DAYS = 30
LAST_RUN_FILE = 'last_run.json'
TORRENT_LOG_FILE = 'torrent_log.json'

# Authenticate with qBittorrent
def qbittorrent_login():
    session = requests.Session()
    try:
        login = session.post(f'{QB_SERVER}/api/v2/auth/login', data={'username': QB_USERNAME, 'password': QB_PASSWORD})
        login.raise_for_status()  # Raises HTTPError for bad responses
        print('Successfully logged into qBittorrent')
    except requests.RequestException as e:
        print(f'Failed to login to qBittorrent: {e}')
        raise
    return session

# Get the last run time for all feeds
def get_last_run():
    if not os.path.exists(LAST_RUN_FILE):
        print('Last run file does not exist, creating a new one.')
        return {}
    try:
        with open(LAST_RUN_FILE, 'r') as f:
            last_run_times = json.load(f)
            print(f'Last run times read from file: {last_run_times}')
            return last_run_times
    except (FileNotFoundError, ValueError) as e:
        print(f'Error reading last run file: {e}')
        return {}

# Update the last run time for a specific feed
def update_last_run(feed_url):
    last_run_times = get_last_run()
    last_run_times[feed_url] = datetime.now().isoformat()
    try:
        with open(LAST_RUN_FILE, 'w') as f:
            json.dump(last_run_times, f)
            print(f'Updated last run time for {feed_url}')
    except IOError as e:
        print(f'Error writing last run file: {e}')

# Log added torrents for removal tracking
def log_torrent(torrent_hash, added_time):
    if not os.path.exists(TORRENT_LOG_FILE):
        print('Torrent log file does not exist, creating a new one.')
        torrent_log = {}
    else:
        try:
            with open(TORRENT_LOG_FILE, 'r') as f:
                torrent_log = json.load(f)
        except (FileNotFoundError, ValueError) as e:
            print(f'Error reading torrent log file: {e}')
            torrent_log = {}

    torrent_log[torrent_hash] = added_time
    try:
        with open(TORRENT_LOG_FILE, 'w') as f:
            json.dump(torrent_log, f)
            print(f'Logged torrent: {torrent_hash} added on {added_time}')
    except IOError as e:
        print(f'Error writing torrent log file: {e}')

# Fetch the latest file from the RSS feed
def fetch_latest_file(feed_url):
    print(f'Fetching RSS feed: {feed_url}')
    feed = feedparser.parse(feed_url)
    latest_entry = None
    latest_time = datetime.min

    for entry in feed.entries:
        published_time = datetime(*entry.published_parsed[:6])
        if published_time > latest_time:
            latest_time = published_time
            latest_entry = entry

    if latest_entry:
        print(f'Found latest file: {latest_entry.title} published on {latest_time}')
    else:
        print(f'No suitable file found in the RSS feed: {feed_url}')
    return latest_entry

# Add torrent to qBittorrent
def add_torrent(session, torrent_url):
    print(f'Adding torrent to qBittorrent: {torrent_url}')
    try:
        add = session.post(f'{QB_SERVER}/api/v2/torrents/add', data={'urls': torrent_url})
        add.raise_for_status()  # Raises HTTPError for bad responses
        print('Torrent added successfully.')
        # Retrieve the torrent hash to log
        torrent_info = session.get(f'{QB_SERVER}/api/v2/torrents/info?filter=all').json()
        for torrent in torrent_info:
            if torrent['magnet_uri'] == torrent_url or torrent['name'] in torrent_url:
                return torrent['hash']
    except requests.RequestException as e:
        print(f'Failed to add torrent to qBittorrent: {e}')
        raise

# Send notification to Discord
def send_discord_notification(message):
    print(f'Sending Discord notification: {message}')
    data = {
        "content": message
    }
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=data)
        response.raise_for_status()  # Raises HTTPError for bad responses
        if response.status_code == 204:
            print('Discord notification sent successfully.')
        else:
            print(f'Unexpected status code from Discord webhook: {response.status_code}')
    except requests.RequestException as e:
        print(f'Failed to send Discord notification: {e}')

# Remove torrents after 30 days
def remove_old_torrents(session):
    print('Checking for torrents to remove...')
    if not os.path.exists(TORRENT_LOG_FILE):
        print('Torrent log file does not exist.')
        return

    try:
        with open(TORRENT_LOG_FILE, 'r') as f:
            torrent_log = json.load(f)
    except (FileNotFoundError, ValueError) as e:
        print(f'Error reading torrent log file: {e}')
        return

    now = datetime.now()
    torrents_to_remove = [hash for hash, added_time in torrent_log.items()
                          if now - datetime.fromisoformat(added_time) > timedelta(days=TORRENT_RETENTION_DAYS)]

    for torrent_hash in torrents_to_remove:
        try:
            remove = session.post(f'{QB_SERVER}/api/v2/torrents/delete', data={'hashes': torrent_hash, 'deleteFiles': True})
            remove.raise_for_status()  # Raises HTTPError for bad responses
            print(f'Torrent {torrent_hash} removed successfully.')
            send_discord_notification(f'Removed torrent with hash: {torrent_hash}')
            del torrent_log[torrent_hash]
        except requests.RequestException as e:
            print(f'Failed to remove torrent with hash: {torrent_hash}: {e}')

    try:
        with open(TORRENT_LOG_FILE, 'w') as f:
            json.dump(torrent_log, f)
    except IOError as e:
        print(f'Error writing torrent log file: {e}')

# Main function
def main():
    print('Running main function...')
    last_run_times = get_last_run()
    session = qbittorrent_login()

    for feed_url in RSS_FEEDS:
        last_run = last_run_times.get(feed_url)
        if last_run and (datetime.now() - datetime.fromisoformat(last_run)) < timedelta(days=INTERVAL_DAYS):
            print(f'Script was last run for {feed_url} on {last_run}, which is within the interval of {INTERVAL_DAYS} days.')
            continue

        latest_file = fetch_latest_file(feed_url)
        if latest_file:
            torrent_url = latest_file.enclosures[0].href
            torrent_hash = add_torrent(session, torrent_url)
            if torrent_hash:
                log_torrent(torrent_hash, datetime.now().isoformat())
                send_discord_notification(f'Added torrent: {latest_file.title}')
                update_last_run(feed_url)
                print(f'Added torrent: {latest_file.title}')
        else:
            print(f'No suitable torrent found in the RSS feed: {feed_url}')

    remove_old_torrents(session)

# Schedule the task
print('Scheduling the task...')
schedule.every(INTERVAL_DAYS).days.do(main)

# Run the scheduler
print('Starting the scheduler...')
main()  # Run the main function once initially
while True:
    schedule.run_pending()
    time.sleep(1)

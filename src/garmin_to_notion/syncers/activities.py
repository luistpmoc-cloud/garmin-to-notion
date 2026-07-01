"""Sync Garmin activities to the Notion Activities database."""

import logging
from typing import Any, Dict, List, Optional

from garminconnect import Garmin
from notion_client import Client

logger = logging.getLogger(__name__)


def _format_duration(seconds: float) -> str:
    """Format a duration in seconds into a human-readable string."""
    if seconds is None:
        return "—"
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _format_distance(meters: Optional[float]) -> str:
    """Format a distance in meters into kilometers."""
    if meters is None:
        return "—"
    return f"{meters / 1000:.2f} km"


def _format_pace(seconds_per_km: Optional[float]) -> str:
    """Format pace (seconds per km) into a mm:ss/km string."""
    if seconds_per_km is None or seconds_per_km <= 0:
        return "—"
    total = int(round(seconds_per_km))
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d} /km"


def _rich_text(content: str) -> List[Dict[str, Any]]:
    """Build a Notion rich text cell value."""
    return [{"type": "text", "text": {"content": content}}]


def fetch_garmin_splits_blocks(
    garmin: Garmin, activity_id: str
) -> List[Dict[str, Any]]:
    """
    Fetch Garmin activity splits (laps) and format them as Notion table blocks.

    The table includes columns: Lap, Distance, Duration, Pace, and Average HR.
    Returns a list containing a single Notion table block (with table_row children)
    suitable for use as children in notion.pages.create / notion.pages.update.
    """
    headers = ["Lap", "Distance", "Duration", "Pace", "Avg HR"]
    rows: List[List[str]] = []

    try:
        splits = garmin.get_activity_splits(activity_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to fetch splits for activity %s: %s", activity_id, exc)
        splits = None

    lap_data: List[Dict[str, Any]] = []
    if splits:
        if isinstance(splits, dict):
            lap_data = splits.get("lapSplits", []) or splits.get("laps", []) or []
        elif isinstance(splits, list):
            lap_data = splits

    for index, lap in enumerate(lap_data, start=1):
        distance = lap.get("distance") or lap.get("distanceMeters")
        duration = lap.get("duration") or lap.get("elapsedDuration")
        avg_hr = lap.get("averageHR") or lap.get("avgHr") or lap.get("averageHeartRate")

        if distance is not None and duration is not None and distance > 0:
            pace = duration / (distance / 1000)
        else:
            pace = None

        rows.append(
            [
                str(index),
                _format_distance(distance),
                _format_duration(duration),
                _format_pace(pace),
                f"{int(avg_hr)} bpm" if avg_hr else "—",
            ]
        )

    table_block: Dict[str, Any] = {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": len(headers),
            "has_column_header": True,
            "has_row_header": False,
            "children": [
                {
                    "type": "table_row",
                    "table_row": {"cells": [_rich_text(h) for h in headers]},
                }
            ],
        },
    }

    for row in rows:
        table_block["table"]["children"].append(
            {"type": "table_row", "table_row": {"cells": [_rich_text(c) for c in row]}}
        )

    return [table_block]


def sync_activities(
    garmin: Garmin,
    notion: Client,
    database_id: str,
    activities: List[Dict[str, Any]],
    existing_pages: Optional[Dict[str, str]] = None,
) -> None:
    """
    Sync Garmin activities into a Notion database.

    For each activity, create or update a Notion page. The page children now
    include a Notion table block with Garmin splits (laps) showing Lap index,
    Distance, Duration, Pace, and Average HR.
    """
    existing_pages = existing_pages or {}

    for activity in activities:
        activity_id = str(activity.get("activityId") or activity.get("id"))
        if not activity_id:
            logger.warning("Skipping activity without an ID: %s", activity)
            continue

        title = activity.get("activityName") or activity.get("title") or f"Activity {activity_id}"
        activity_type = activity.get("activityType", {}).get("typeKey", "unknown") if isinstance(activity.get("activityType"), dict) else activity.get("type", "unknown")
        start_time = activity.get("startTimeLocal") or activity.get("startTime") or ""
        distance = activity.get("distance", 0)
        duration = activity.get("duration", 0)

        properties = {
            "Title": {"title": [{"text": {"content": title}}]},
            "Type": {"select": {"name": activity_type}},
            "Date": {"date": {"start": start_time}} if start_time else {},
            "Distance": {"number": distance},
            "Duration": {"number": duration},
        }

        splits_blocks = fetch_garmin_splits_blocks(garmin, activity_id)

        if activity_id in existing_pages:
            page_id = existing_pages[activity_id]
            logger.info("Updating Notion page for activity %s", activity_id)
            notion.pages.update(page_id=page_id, properties=properties)
            notion.blocks.children.append(block_id=page_id, children=splits_blocks)
        else:
            logger.info("Creating Notion page for activity %s", activity_id)
            notion.pages.create(
                parent={"database_id": database_id},
                properties=properties,
                children=splits_blocks,
            )

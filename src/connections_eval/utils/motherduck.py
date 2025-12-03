"""Utilities for uploading controllog files to MotherDuck and validation."""

import os
import sys
import shutil
import json
from pathlib import Path
from datetime import datetime
from typing import Optional
import duckdb  # type: ignore

# Import functions from scripts directory
# Add scripts directory to path for imports
_scripts_dir = Path(__file__).parent.parent.parent.parent / "scripts"
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

from load_controllog_to_motherduck import load_directory  # type: ignore
from reports_controllog import trial_balance  # type: ignore


def upload_controllog_to_motherduck(log_path: Path, db: str) -> bool:
    """
    Upload controllog files to MotherDuck.
    
    Args:
        log_path: Base log directory containing controllog subdirectory
        db: MotherDuck database connection string (e.g., "md:controllog")
        
    Returns:
        True if upload succeeded, False otherwise
    """
    try:
        load_directory(log_path, db)
        return True
    except Exception as e:
        print(f"Error uploading to MotherDuck: {e}")
        return False


def validate_upload(run_id: str, db: str) -> bool:
    """
    Validate that the run's events/postings exist in MotherDuck.
    
    Args:
        run_id: The run_id to validate
        db: MotherDuck database connection string
        
    Returns:
        True if validation passed, False otherwise
    """
    try:
        con = duckdb.connect(db)
        
        # Check if events exist for this run_id
        events_result = con.execute(
            "SELECT COUNT(*) FROM controllog.events WHERE run_id = ?",
            [run_id]
        ).fetchone()
        
        event_count = events_result[0] if events_result else 0
        
        # Check if postings exist for events from this run_id
        postings_result = con.execute(
            """
            SELECT COUNT(*) 
            FROM controllog.postings p
            JOIN controllog.events e ON p.event_id = e.event_id
            WHERE e.run_id = ?
            """,
            [run_id]
        ).fetchone()
        
        postings_count = postings_result[0] if postings_result else 0
        
        con.close()
        
        # Validation passes if we have at least some events
        # (postings may be zero if no resource tracking occurred)
        return event_count > 0
        
    except Exception as e:
        print(f"Error validating upload: {e}")
        return False


def run_trial_balance(db: str) -> bool:
    """
    Run trial balance check on MotherDuck database.
    
    Args:
        db: MotherDuck database connection string
        
    Returns:
        True if trial balance passed, False otherwise
    """
    try:
        con = duckdb.connect(db)
        trial_balance(con)
        con.close()
        return True
    except RuntimeError as e:
        print(f"Trial balance failed: {e}")
        return False
    except Exception as e:
        print(f"Error running trial balance: {e}")
        return False


def cleanup_local_files(log_path: Path, run_id: str, keep_files: bool) -> None:
    """
    Delete controllog files if keep_files is False.
    
    Since multiple runs can share the same date-partitioned directory, this function
    filters the JSONL files to remove only lines related to this run_id.
    
    Args:
        log_path: Base log directory containing controllog subdirectory
        run_id: The run_id to identify which files to clean up
        keep_files: If True, keep files; if False, delete them
    """
    if keep_files:
        return
    
    try:
        # Extract date from run_id (format: YYYY-MM-DDTHH-MM-SS_model)
        date_str = run_id.split("T")[0] if "T" in run_id else None
        
        if not date_str:
            # Fallback to today's date if we can't parse it
            date_str = datetime.utcnow().strftime("%Y-%m-%d")
        
        controllog_dir = log_path / "controllog" / date_str
        if not controllog_dir.exists() or not controllog_dir.is_dir():
            return
        
        events_file = controllog_dir / "events.jsonl"
        postings_file = controllog_dir / "postings.jsonl"
        
        # Check if files exist and filter them
        events_updated = False
        postings_updated = False
        
        # Filter events.jsonl - remove lines for this run_id
        if events_file.exists():
            event_ids_to_remove = set()
            filtered_events = []
            
            with open(events_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        if event.get("run_id") == run_id:
                            event_ids_to_remove.add(event.get("event_id"))
                            events_updated = True
                        else:
                            filtered_events.append(line)
                    except json.JSONDecodeError:
                        # Keep malformed lines
                        filtered_events.append(line)
            
            # Write filtered events back
            if events_updated:
                with open(events_file, 'w', encoding='utf-8') as f:
                    for line in filtered_events:
                        f.write(line + '\n')
        
        # Filter postings.jsonl - remove postings for events we removed
        if postings_file.exists() and event_ids_to_remove:
            filtered_postings = []
            
            with open(postings_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        posting = json.loads(line)
                        if posting.get("event_id") not in event_ids_to_remove:
                            filtered_postings.append(line)
                        else:
                            postings_updated = True
                    except json.JSONDecodeError:
                        # Keep malformed lines
                        filtered_postings.append(line)
            
            # Write filtered postings back
            if postings_updated:
                with open(postings_file, 'w', encoding='utf-8') as f:
                    for line in filtered_postings:
                        f.write(line + '\n')
        
        # If both files are now empty, remove the directory
        if events_updated or postings_updated:
            if events_file.exists() and events_file.stat().st_size == 0:
                events_file.unlink()
            if postings_file.exists() and postings_file.stat().st_size == 0:
                postings_file.unlink()
            
            # Remove directory if it's now empty
            try:
                if not any(controllog_dir.iterdir()):
                    controllog_dir.rmdir()
                    print(f"Cleaned up empty controllog directory: {controllog_dir}")
                else:
                    print(f"Cleaned up controllog files for run_id: {run_id}")
            except OSError:
                # Directory not empty or other error, that's fine
                print(f"Cleaned up controllog files for run_id: {run_id}")
                                
    except Exception as e:
        print(f"Warning: Error cleaning up local files: {e}")


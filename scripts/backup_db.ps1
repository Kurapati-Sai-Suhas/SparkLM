# backup_db.ps1
# 
# Description: Automated PostgreSQL database backup script for LearnLM.
# Usage: Run this script via Windows Task Scheduler for daily automated backups.
# Note: Ensure pg_dump is in your PATH.

$ErrorActionPreference = "Stop"

# Configuration
$DB_NAME = "learnlm_db"
$DB_USER = "postgres" # Replace with actual production user if different
$BACKUP_DIR = "C:\Users\Suhas\OneDrive\Documents\Notes\Project1683\LearnLM\backups"

# Ensure backup directory exists
if (-Not (Test-Path -Path $BACKUP_DIR)) {
    New-Item -ItemType Directory -Path $BACKUP_DIR | Out-Null
    Write-Host "Created backup directory at $BACKUP_DIR"
}

# Generate timestamp
$TIMESTAMP = Get-Date -Format "yyyyMMdd_HHmmss"
$BACKUP_FILE = "$BACKUP_DIR\${DB_NAME}_backup_${TIMESTAMP}.sql"

Write-Host "Starting backup of database '$DB_NAME'..."

try {
    # Run pg_dump (Assuming password is provided via pgpass.conf or PGPASSWORD env var)
    # If a password is required, you can set it temporarily like this:
    # $env:PGPASSWORD="your_password"
    
    pg_dump -U $DB_USER -d $DB_NAME -F p -f $BACKUP_FILE
    
    Write-Host "Backup completed successfully! Saved to: $BACKUP_FILE" -ForegroundColor Green
}
catch {
    Write-Host "Backup failed! Error: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

# Optional: Cleanup backups older than 7 days
$DAYS_TO_KEEP = 7
$OLD_BACKUPS = Get-ChildItem -Path $BACKUP_DIR -Filter "*.sql" | Where-Object { $_.CreationTime -lt (Get-Date).AddDays(-$DAYS_TO_KEEP) }

if ($OLD_BACKUPS) {
    Write-Host "Cleaning up backups older than $DAYS_TO_KEEP days..."
    $OLD_BACKUPS | Remove-Item
    Write-Host "Cleanup complete."
}

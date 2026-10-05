# Automated GitHub Project Automation Rules Setup for Waypost
# This script sets up automation rules for the existing project board

$repoOwner = "sean7084"
$repoName = "Waypost"
# Read GITHUB_CLASSIC_TOKEN from .env (the documented local secrets file),
# falling back to the legacy .env.local if that is what exists.
$envFile = @('.env', '.env.local') | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $envFile) { throw "No .env (or .env.local) found in the repository root" }
$token = (Get-Content $envFile | Select-String '^GITHUB_CLASSIC_TOKEN=').ToString().Split('=')[1]
$headers = @{
    "Authorization" = "Bearer $token"
    "Accept" = "application/vnd.github.v3+json"
}
$baseUrl = "https://api.github.com/repos/$repoOwner/$repoName"

Write-Host "=== Starting Project Automation Rules Setup ===" -ForegroundColor Cyan
Write-Host "Project URL: https://github.com/users/sean7084/projects/1" -ForegroundColor Yellow

# Note: GitHub Projects Classic uses a different automation system
# For manual setup guidance, refer to the project's "Automate" button

$automationGuide = @"

📋 PROJECT AUTOMATION RULES - MANUAL SETUP GUIDE

Your project board is ready at: https://github.com/users/sean7084/projects/1

To add automation rules:

1️⃣ Click the "Automate" dropdown button at the top of your project board

2️⃣ Add these recommended automation rules:

RULE 1: Pull Request Creation
├─ Trigger: When pull request is created
└─ Action: Move card to column "Code Review"

RULE 2: Pull Request Merged
├─ Trigger: When pull request is merged  
└─ Action: Move card to column "Testing"

RULE 3: Issue with Bug Label
├─ Trigger: When issue labeled bug
└─ Action: If assigned, move to "In Progress"

RULE 4: Stale Issues
├─ Trigger: When no activity for 30 days
└─ Action: Archive card (do not delete)

💡 TIPS:
• All rules can be modified or removed anytime
• You can add custom rules beyond these templates
• Automation works for issues, PRs, and tasks

📖 For complete details, see: .github/PROJECT_WORKFLOW.md

"@

Write-Host "`n$automationGuide" -ForegroundColor Cyan

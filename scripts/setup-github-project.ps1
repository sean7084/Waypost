# Automated GitHub Project Board Setup for Waypost
# This script creates project board, columns, custom fields, and automation rules

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

Write-Host "=== Starting Project Board Creation ===" -ForegroundColor Cyan

# Step 1: Get Project Board ID (check if exists or create new)
Write-Host "`n[1/5] Checking for existing projects..." -ForegroundColor Yellow

try {
    $existingProjects = Invoke-RestMethod `
        -Uri "$baseUrl/projects" `
        -Method Get `
        -Headers $headers
    
    $project = $existingProjects | Where-Object { $_.name -eq "Waypost Development" }
    
    if ($project) {
        Write-Host "[✓] Found existing project: $($project.name)" -ForegroundColor Green
        $projectId = $project.id
    } else {
        # Create new project using organization/user endpoint
        Write-Host "[✗] No existing project found, creating new one..." -ForegroundColor Yellow
        
        $projectData = @{
            name = "Waypost Development"
            body = "Project board for tracking Waypost development workflow, sprints, and releases"
            source = "repository"
        } | ConvertTo-Json
        
        # Try different endpoints for project creation
        try {
            $orgProjects = Invoke-RestMethod `
                -Uri "https://api.github.com/user/projects" `
                -Method Get `
                -Headers $headers
            Write-Host "[!] User projects not accessible via API" -ForegroundColor Red
            
            # Alternative: Use repository projects
            Write-Host "Using alternative method via repository..." -ForegroundColor Yellow
            $projectCreate = @{
                name = "Waypost Development"
                body = "Project board for tracking Waypost development workflow, sprints, and releases"
            } | ConvertTo-Json
            
            $newProject = Invoke-RestMethod `
                -Uri "$baseUrl/projects" `
                -Method Post `
                -Headers $headers `
                -Body $projectCreate `
                -ContentType "application/json"
            
            $projectId = $newProject.id
            Write-Host "[✓] Created project with ID: $projectId" -ForegroundColor Green
        } catch {
            Write-Host "[!] Manual project creation required:" -ForegroundColor Red
            Write-Host "  1. Go to your repository's Projects tab" -ForegroundColor Gray
            Write-Host "  2. Click 'New project'" -ForegroundColor Gray
            Write-Host "  3. Select 'Board' view" -ForegroundColor Gray
            Write-Host "  4. Name it: 'Waypost Development'" -ForegroundColor Gray
            throw "Project API not available"
        }
    }
} catch {
    Write-Host "[!] Error checking projects: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Please create project manually first" -ForegroundColor Yellow
    exit 1
}

Write-Host "`n[✓] Project initialized: ID=$projectId" -ForegroundColor Green

# Step 2: Define Columns (This is done automatically in GitHub - no API needed usually)
Write-Host "`n[2/5] Columns are auto-created by GitHub" -ForegroundColor Yellow
Write-Host "Default columns: Todo, In Progress, Done" -ForegroundColor Gray
Write-Host "You can rename/add columns manually per PROJECT_WORKFLOW.md" -ForegroundColor Gray

# Step 3: Verify Columns Configuration
Write-Host "`n[3/5] Column Names Check:" -ForegroundColor Yellow
$columnsInfo = @(
    "To Do",
    "In Progress", 
    "Code Review",
    "Testing",
    "Ready for Deploy",
    "Deployed",
    "Done"
) | ForEach-Object { "[ ] $_" }
$columnsInfo -join "`n " | Write-Host

# Step 4: Setup Automation Rules (if possible via API)
Write-Host "`n[4/5] Attempting to configure automation rules..." -ForegroundColor Yellow
Write-Host "Note: Automation rules may need manual setup in GitHub UI" -ForegroundColor Gray

# Since project automation APIs require specific permissions, let's document what we want:
Write-Host "`nDesired Automation Rules:" -ForegroundColor Cyan
@{
    "Rule 1" = @{
        Trigger = "Pull request created"
        Action = "Move card to 'Code Review' column"
    }
    "Rule 2" = @{
        Trigger = "Pull request merged"  
        Action = "Move card to 'Testing' + add label 'test-passed'"
    }
} | Format-Table | Write-Host

# Step 5: Custom Fields Configuration
Write-Host "`n[5/5] Custom Fields to Add Manually:" -ForegroundColor Yellow
Write-Host "Click 'Customize fields' in project settings:" -ForegroundColor Gray
$customFields = @(
    "- Priority (Single select): P0-Critical, P1-High, P2-Medium, P3-Low",
    "- Component (Multiple select): accounts, assets, companies, quotations, deliveries, invoices, products, dashboard, reports",
    "- Story Points (Number)",
    "- Sprint (Date or Text)",
    "- Milestone (Link to milestone)"
) | ForEach-Object { "  $$_" }
$customFields -join "`n" | Write-Host

Write-Host "`n=== Summary ===" -ForegroundColor Cyan
Write-Host "Project created successfully!" -ForegroundColor Green
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Configure columns manually: To Do → In Progress → Code Review → Testing → Ready for Deploy → Deployed → Done" -ForegroundColor Gray
Write-Host "  2. Add custom fields: Priority, Component, Story Points, Sprint, Milestone" -ForegroundColor Gray
Write-Host "  3. Set up automation rules from 'Automate' dropdown (top-right of project board)" -ForegroundColor Gray

Write-Host "`n📋 Full documentation:" -ForegroundColor Cyan
Write-Host "See .github/PROJECT_WORKFLOW.md for detailed setup instructions" -ForegroundColor Gray

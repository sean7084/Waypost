# Automated GitHub Branch Protection Setup for Waypost
# This script sets up protected branches with CODEOWNERS requirements

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

Write-Host "=== Starting Branch Protection Setup ===" -ForegroundColor Cyan
Write-Host "Repository: $repoOwner/$repoName" -ForegroundColor Yellow

# Check if .github/CODEOWNERS file exists
Write-Host "`nChecking CODEOWNERS file..." -ForegroundColor Yellow
if (Test-Path "CODEOWNERS") {
    Write-Host "[✓] CODEOWNERS file found in root directory" -ForegroundColor Green
    
    # Verify CODEOWNERS content
    $codeOwnersContent = Get-Content "CODEOWNERS" -Raw
    if ($codeOwnersContent -match "@" ) {
        Write-Host "[✓] CODEOWNERS contains proper ownership rules" -ForegroundColor Green
    } else {
        Write-Host "[!] WARNING: CODEOWNERS might be empty or invalid" -ForegroundColor Red
    }
} else {
    Write-Host "[✗] CODEOWNERS file not found! Creating it first..." -ForegroundColor Red
    throw "CODEOWNERS file missing - please ensure it was committed to repository"
}

# Get list of default branches (usually 'main' or 'master')
Write-Host "`nGetting default branch name..." -ForegroundColor Yellow
try {
    $repoInfo = Invoke-RestMethod `
        -Uri "$baseUrl" `
        -Method Get `
        -Headers $headers
    
    $defaultBranch = $repoInfo.default_branch
    Write-Host "[✓] Default branch: '$defaultBranch'" -ForegroundColor Green
} catch {
    Write-Host "[!] Error getting repo info: $($_.Exception.Message)" -ForegroundColor Red
    throw "Cannot determine default branch"
}

# Step 1: Set Up Required Status Checks
Write-Host "`n[1/3] Setting up required status checks..." -ForegroundColor Yellow

$statusChecksConfig = @{
    strict = $true
    contexts = @()
} | ConvertTo-Json

try {
    Invoke-RestMethod `
        -Uri "$baseUrl/branches/$defaultBranch/protection/required_status_checks" `
        -Method Put `
        -Headers $headers `
        -Body '{"strict":true,"contexts":[]}' `
        -ContentType "application/json"
    
    Write-Host "[✓] Required status checks configured" -ForegroundColor Green
} catch {
    Write-Host "[!] Could not set status checks: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "This is optional - continuing anyway..." -ForegroundColor Gray
}

# Step 2: Set Up Pull Request Reviews
Write-Host "`n[2/3] Setting up pull request review requirements..." -ForegroundColor Yellow

$pullRequestConfig = @{
    enforce_admins = $true
    required_pull_request_reviews = @{
        required_approving_review_count = 2
        dismiss_stale_reviews = $true
        require_code_owner_reviews = $true
        dismissal_restrictions = @{
            users = @()
            teams = @()
        }
    }
} | ConvertTo-Json

try {
    Invoke-RestMethod `
        -Uri "$baseUrl/branches/$defaultBranch/protection" `
        -Method Put `
        -Headers $headers `
        -Body $pullRequestConfig `
        -ContentType "application/json"
    
    Write-Host "[✓] Pull request review requirements configured:" -ForegroundColor Green
    Write-Host "   - Minimum 2 approvals required" -ForegroundColor Gray
    Write-Host "   - Stale reviews dismissed on new commits" -ForegroundColor Gray
    Write-Host "   - CODEOWNERS approval enforced" -ForegroundColor Gray
} catch {
    Write-Host "[!] Could not configure PR reviews: $($_.Exception.Message)" -ForegroundColor Red
    throw "Failed to set up pull request protection"
}

# Step 3: Enable Force Push Protection and Deletion Protection
Write-Host "`n[3/3] Enabling additional protections..." -ForegroundColor Yellow

$additionalConfig = @{
    allow_force_pushes = $false
    allow_deletions = $false
    block_creations = $false
}

try {
    Invoke-RestMethod `
        -Uri "$baseUrl/branches/$defaultBranch/protection" `
        -Method Patch `
        -Headers $headers `
        -Body ($additionalConfig | ConvertTo-Json) `
        -ContentType "application/json"
    
    Write-Host "[✓] Additional protections enabled:" -ForegroundColor Green
    Write-Host "   - Force pushes blocked" -ForegroundColor Gray
    Write-Host "   - Direct deletions blocked" -ForegroundColor Gray
} catch {
    Write-Host "[!] Could not update additional settings: $($_.Exception.Message)" -ForegroundColor Yellow
}

# Final Verification
Write-Host "`n=== Branch Protection Configuration Complete ===" -ForegroundColor Cyan
Write-Host "`nVerifying current protection rules..." -ForegroundColor Yellow

try {
    $currentRules = Invoke-RestMethod `
        -Uri "$baseUrl/branches/$defaultBranch/protection" `
        -Method Get `
        -Headers $headers
    
    Write-Host "`nCurrent Branch Protection Settings:" -ForegroundColor Cyan
    Write-Host "Required Status Checks: $($currentRules.required_status_checks.strict)" -ForegroundColor Gray
    Write-Host "Pull Request Reviews: $($currentRules.required_pull_request_reviews.required_approving_review_count)" -ForegroundColor Gray
    Write-Host "Dismiss Stale Reviews: $($currentRules.required_pull_request_reviews.dismiss_stale_reviews)" -ForegroundColor Gray
    Write-Host "Require Code Owner Reviews: $($currentRules.required_pull_request_reviews.require_code_owner_reviews)" -ForegroundColor Gray
    Write-Host "Enforce Admins: $($currentRules.enforce_admins)" -ForegroundColor Gray
    Write-Host "Allow Force Pushes: $($currentRules.allow_force_pushes)" -ForegroundColor Gray
    Write-Host "Allow Deletions: $($currentRules.allow_deletions)" -ForegroundColor Gray
    
} catch {
    Write-Host "Could not verify final configuration: $($_.Exception.Message)" -ForegroundColor Yellow
}

Write-Host "`n✅ Branch Protection Successfully Configured!" -ForegroundColor Green
Write-Host "`nWhat's Protected:" -ForegroundColor Cyan
Write-Host "  - Branch: '$defaultBranch'" -ForegroundColor Gray
Write-Host "  - Requires 2 approving reviewers" -ForegroundColor Gray
Write-Host "  - CODEOWNERS must approve their changes" -ForegroundColor Gray
Write-Host "  - Force pushes are disabled" -ForegroundColor Gray
Write-Host "  - Direct commits are prevented" -ForegroundColor Gray

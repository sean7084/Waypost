# Branch Protection Setup for Waypost
# ============================================================================
# Configures branch protection rules for the `main` branch using the
# GitHub REST API. This is the CANONICAL branch-protection script; the older
# `setup-github-branch-protection.ps1` is superseded.
#
# Strategy: Option C - strict protection WITH admin bypass.
#   The repository owner can merge their own PRs (solo development), while
#   any future collaborator is forced through the review process.
#
# Prerequisites:
#   - GITHUB_CLASSIC_TOKEN present in .env
#   - Token scopes: 'repo' (required); 'admin:repo_hook' and 'project' help
#
# Personal-repository constraints (why this script looks the way it does):
#   - 'restrictions' / 'dismissal_restrictions' with users or teams are
#     REJECTED (HTTP 422): "Only organization repositories can have users
#     and team restrictions". They are omitted / set to null here.
#   - The GitHub UI's "Allow specified actors to bypass" option is
#     organization-only. For a personal repo, enforce_admins = $false is the
#     equivalent and is what enables the owner bypass.
#
# Full settings rationale: docs/GITHUB_SETTINGS.md
# ============================================================================

$repoOwner = "sean7084"
$repoName = "Waypost"
$branch = "main"

# Read token from .env (falling back to the legacy .env.local)
$envFile = @('.env', '.env.local') | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $envFile) {
    Write-Host "ERROR: no .env (or .env.local) found in the repository root" -ForegroundColor Red
    exit 1
}
$content = Get-Content $envFile -Raw
if ($content -match 'GITHUB_CLASSIC_TOKEN=(\S+)') {
    $token = $Matches[1].Trim()
} else {
    Write-Host "ERROR: GITHUB_CLASSIC_TOKEN not found in $envFile" -ForegroundColor Red
    Write-Host "Please add your GitHub token to .env" -ForegroundColor Yellow
    exit 1
}

$headers = @{
    "Authorization" = "Bearer $token"
    "Accept" = "application/vnd.github.v3+json"
}

$apiUrl = "https://api.github.com/repos/$repoOwner/$repoName/branches/$branch/protection"

Write-Host "=== Setting Up Branch Protection ===" -ForegroundColor Cyan
Write-Host "Repository: $repoOwner/$repoName" -ForegroundColor Yellow
Write-Host "Branch: $branch" -ForegroundColor Yellow
Write-Host ""

# First, verify token works
try {
    $user = Invoke-RestMethod -Uri "https://api.github.com/user" -Headers $headers
    Write-Host "[✓] Token valid. Logged in as: $($user.login)" -ForegroundColor Green
} catch {
    Write-Host "[✗] Token authentication failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ""
    Write-Host "Your token may be expired or lack permissions." -ForegroundColor Yellow
    Write-Host "To fix this:" -ForegroundColor Yellow
    Write-Host "1. Go to: https://github.com/settings/tokens" -ForegroundColor Cyan
    Write-Host "2. Generate a new token with these scopes:" -ForegroundColor Cyan
    Write-Host "   - repo (full control of private repositories)" -ForegroundColor Gray
    Write-Host "   - admin:repo (if available)" -ForegroundColor Gray
    Write-Host "3. Update GITHUB_CLASSIC_TOKEN in .env" -ForegroundColor Cyan
    Write-Host "4. Re-run this script" -ForegroundColor Cyan
    exit 1
}

# Configure branch protection (Option C: strict protection + admin bypass)
#
# enforce_admins = $false  -> the repository owner (admin) may bypass the
#   review requirements and merge their own PRs. Collaborators cannot.
#   This is the personal-repo equivalent of "allow specified actors to bypass".
#
# required_pull_request_reviews:
#   required_approving_review_count = 1  -> one approval needed (non-admins)
#   require_code_owner_reviews = $true   -> CODEOWNERS must approve
#   dismiss_stale_reviews = $true        -> approvals reset on new commits
#
# restrictions = $null:
#   restrictions is org-only (see header).
#
# required_status_checks:
#   Both contexts are jobs in .github/workflows/backend-ci.yml, which deliberately
#   has NO path filter: a required check that never runs on a given PR (e.g. a
#   docs-only PR under a paths-ignore filter) blocks that PR forever.
#   The 'Lint mini program' check IS path-filtered to miniprogram/** and is
#   therefore intentionally NOT required here.
#   strict = $true -> a PR must be up to date with main before merging, so the
#   commit that was tested is the commit that lands.
#
# See docs/GITHUB_SETTINGS.md for the complete decision record.
$protectionConfig = @{
    required_status_checks = @{
        strict = $true
        contexts = @("Django checks + tests", "Python lint (ruff)")
    }
    enforce_admins = $false
    required_pull_request_reviews = @{
        dismiss_stale_reviews = $true
        require_code_owner_reviews = $true
        required_approving_review_count = 1
    }
    restrictions = $null
} | ConvertTo-Json -Depth 5

Write-Host "Applying branch protection configuration..." -ForegroundColor Cyan
Write-Host "Request body:" -ForegroundColor Gray
Write-Host $protectionConfig -ForegroundColor DarkGray
Write-Host ""

try {
    $response = Invoke-RestMethod `
        -Uri $apiUrl `
        -Method Put `
        -Headers $headers `
        -Body $protectionConfig `
        -ContentType "application/json"
    
    Write-Host ""
    Write-Host "=== SUCCESS ===" -ForegroundColor Green
    Write-Host "Branch protection configured for '$branch' branch! (Option C)" -ForegroundColor Green
    Write-Host ""
    Write-Host "Applied settings:" -ForegroundColor Cyan
    Write-Host "  ✓ Require pull request before merging" -ForegroundColor Green
    Write-Host "  ✓ Require 1 approving review" -ForegroundColor Green
    Write-Host "  ✓ Dismiss stale reviews automatically" -ForegroundColor Green
    Write-Host "  ✓ Require review from Code Owners" -ForegroundColor Green
    Write-Host "  ✓ Require status checks 'Django checks + tests' and 'Python lint (ruff)'" -ForegroundColor Green
    Write-Host "  ✓ Admin (owner) can bypass requirements" -ForegroundColor Green
    Write-Host "  ✓ Block force pushes" -ForegroundColor Green
    Write-Host "  ✓ Block branch deletion" -ForegroundColor Green
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Yellow
    Write-Host "  1. Test by creating a PR to main branch" -ForegroundColor Gray
    Write-Host "  2. Verify CODEOWNERS triggers automatic review requests" -ForegroundColor Gray
    Write-Host "  3. Confirm collaborators need approval but admin can bypass" -ForegroundColor Gray
    
} catch {
    Write-Host ""
    Write-Host "=== FAILED ===" -ForegroundColor Red
    Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red
    
    # Try to get detailed error using curl
    Write-Host ""
    Write-Host "Getting detailed error from GitHub API..." -ForegroundColor Cyan
    $curlCommand = "curl -s -X PUT -H `"Authorization: Bearer $token`" -H `"Accept: application/vnd.github.v3+json`" -H `"Content-Type: application/json`" -d `'$protectionConfig`' `"$apiUrl`""
    $detailedError = Invoke-Expression $curlCommand 2>&1
    Write-Host "GitHub API Response:" -ForegroundColor Yellow
    Write-Host $detailedError -ForegroundColor Gray
    
    Write-Host ""
    Write-Host "Troubleshooting:" -ForegroundColor Yellow
    Write-Host "  • Ensure token has 'repo' scope" -ForegroundColor Gray
    Write-Host "  • Verify you're a repository admin" -ForegroundColor Gray
    Write-Host "  • Check if branch exists: git branch -a" -ForegroundColor Gray
}

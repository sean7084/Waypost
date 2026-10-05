# Automated GitHub Label Setup for Waypost
# This script creates all ~33 labels with proper colors and descriptions

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

# Define all labels to create
$labels = @(
    @{name="triage"; color="#E9D75F"; description="Needs initial review and categorization"}
    @{name="awaiting response"; color="#0E8A16"; description="Waiting on author feedback"}
    @{name="blocked"; color="#BDBDBD"; description="Cannot proceed due to dependency"}
    @{name="stale"; color="#EEEEEE"; description="No activity for 30 days"}
    
    @{name="P0 - Critical"; color="#B60205"; description="Blocks release; needs immediate fix"}
    @{name="P1 - High"; color="#D93F0B"; description="Important bug/feature for next sprint"}
    @{name="P2 - Medium"; color="#FBCA04"; description="Regular backlog item"}
    @{name="P3 - Low"; color="#EDEDED"; description="Nice-to-have, low priority"}
    
    @{name="bug"; color="#D73A4A"; description="Something isn't working correctly"}
    @{name="enhancement"; color="#A2EEEF"; description="New feature request"}
    @{name="documentation"; color="#0075CA"; description="Improvements to docs"}
    @{name="good first issue"; color="#7057FF"; description="Easy for newcomers"}
    @{name="help wanted"; color="#008672"; description="Contribution needed"}
    @{name="question"; color="#CC3147"; description="More information needed"}
    @{name="discussion"; color="#CCE329"; description="Topics requiring community input"}
    
    @{name="component: accounts"; color="#1D76EB"; description="User management, authentication"}
    @{name="component: assets"; color="#0052CC"; description="Asset CRUD, assignments"}
    @{name="component: companies"; color="#54AE5F"; description="Company/location structures"}
    @{name="component: quotations"; color="#FBE75F"; description="Quotation lifecycle"}
    @{name="component: deliveries"; color="#FBF29D"; description="Delivery orders"}
    @{name="component: invoices"; color="#A2EEEF"; description="Invoice processing"}
    @{name="component: products"; color="#5791EF"; description="Price list, service catalog"}
    @{name="component: dashboard"; color="#FFFFFF"; description="Main landing page"}
    @{name="component: reports"; color="#79CB23"; description="Analytics & charts"}
    @{name="component: api"; color="#0E8A16"; description="REST API surface (/api/v1)"}
    @{name="component: inspections"; color="#5319E7"; description="Kering store device-inspection domain"}
    @{name="component: miniprogram"; color="#07A162"; description="WeChat mini program client"}
    @{name="component: import"; color="#D4C5F9"; description="Data import pipelines"}
    
    @{name="breaking change"; color="#B60205"; description="Incompatible API/model changes"}
    @{name="deprecation"; color="#D8744E"; description="Deprecated feature scheduled for removal"}
    @{name="migration-required"; color="#BF5D73"; description="Database migration required"}
    
    @{name="needs testing"; color="#FFEBD6"; description="Awaiting QA validation"}
    @{name="test-passed"; color="#84b6ef"; description="Verified in staging"}
    @{name="e2e-test-needed"; color="#7057FF"; description="End-to-end test coverage gap"}
    
    @{name="deployment"; color="#C2E0C6"; description="Ready for deployment"}
    @{name="release-notes"; color="#0366D6"; description="Must be included in changelog"}
    @{name="security"; color="#EB6420"; description="Security-related fix"}
    @{name="performance"; color="#C6CFCF"; description="Speed/performance improvement"}
)

Write-Host "Starting label creation process..." -ForegroundColor Cyan
Write-Host "Repository: $repoOwner/$repoName" -ForegroundColor Yellow
Write-Host "Total labels to create: $($labels.Count)`n"

$createdCount = 0
$errorCount = 0

foreach ($label in $labels) {
    try {
        $body = @{
            name = $label.name
            color = $label.color.Trim('#')
            description = $label.description
        } | ConvertTo-Json
        
        $response = Invoke-RestMethod `
            -Uri "$baseUrl/labels" `
            -Method Post `
            -Headers $headers `
            -Body $body `
            -ContentType "application/json"
        
        Write-Host "[✓] Created: $($label.name)" -ForegroundColor Green
        $createdCount++
    } catch {
        Write-Host "[✗] Failed to create: $($label.name)" -ForegroundColor Red
        Write-Host "  Error: $($_.Exception.Message)" -ForegroundColor Gray
        $errorCount++
    }
}

Write-Host "`n=== Summary ===" -ForegroundColor Cyan
Write-Host "Labels created successfully: $createdCount / $($labels.Count)" -ForegroundColor Green
if ($errorCount -gt 0) {
    Write-Host "Errors encountered: $errorCount" -ForegroundColor Red
} else {
    Write-Host "All labels created successfully!" -ForegroundColor Green
}

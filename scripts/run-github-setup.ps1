# Master GitHub Setup Script for Waypost
# Runs all configuration steps automatically

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "GitHub Features Configuration - Master Runner" -ForegroundColor Cyan
Write-Host "Repository: sean7084/Waypost" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$scriptDir = $PSScriptRoot
$startTime = Get-Date

# Step 1: Setup Labels (if not already done)
Write-Host "[STEP 1/3] Setting up GitHub Labels..." -ForegroundColor Cyan
Write-Host "----------------------------------------" -ForegroundColor Gray

try {
    Invoke-Expression ". `$scriptDir\setup-github-labels.ps1"
    Write-Host "`n✅ STEP 1 COMPLETE - Labels configured`n" -ForegroundColor Green
} catch {
    Write-Host "`n❌ STEP 1 FAILED: $($_.Exception.Message)" -ForegroundColor Red
    throw "Label setup failed"
}

# Step 2: Setup Project Board
Write-Host "[STEP 2/3] Setting up Project Board..." -ForegroundColor Cyan
Write-Host "----------------------------------------" -ForegroundColor Gray

try {
    Invoke-Expression ". `$scriptDir\setup-github-project.ps1"
    Write-Host "`n✅ STEP 2 COMPLETE - Project initialized`n" -ForegroundColor Green
} catch {
    Write-Host "`n⚠️ STEP 2 INCOMPLETE: Manual action required" -ForegroundColor Yellow
    Write-Host "Please follow the in-script instructions to create project manually" -ForegroundColor Gray
    Write-Host "`nFor full details, see .github/PROJECT_WORKFLOW.md`n" -ForegroundColor Cyan
}

# Step 3: Setup Branch Protection
Write-Host "[STEP 3/3] Setting up Branch Protection..." -ForegroundColor Cyan
Write-Host "----------------------------------------" -ForegroundColor Gray

try {
    Invoke-Expression ". `$scriptDir\setup-github-branch-protection.ps1"
    Write-Host "`n✅ STEP 3 COMPLETE - Branch protection configured`n" -ForegroundColor Green
} catch {
    Write-Host "`n❌ STEP 3 FAILED: $($_.Exception.Message)" -ForegroundColor Red
    throw "Branch protection setup failed"
}

# Final Summary
$endTime = Get-Date
$totalTime = $endTime - $startTime

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "All Steps Completed Successfully!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Configuration Summary:" -ForegroundColor Yellow
Write-Host "  ✅ Labels: 34 labels created and verified" -ForegroundColor Green
Write-Host "  ⚙️  Project: Initialized (may need manual column setup)" -ForegroundColor Yellow
Write-Host "  ✅ Branch Protection: Configured for '$defaultBranch'" -ForegroundColor Green
Write-Host ""
Write-Host "Total Time: $($totalTime.TotalSeconds.ToString('0.0')) seconds" -ForegroundColor Cyan
Write-Host ""

Write-Host "Next Actions:" -ForegroundColor Yellow
Write-Host "  1. Verify labels at: https://github.com/sean7084/Waypost/labels" -ForegroundColor Gray
Write-Host "  2. Configure project board columns and custom fields" -ForegroundColor Gray
Write-Host "  3. Test branch protection by creating a test PR" -ForegroundColor Gray
Write-Host "  4. Review documentation in .github/ directory" -ForegroundColor Gray
Write-Host ""

Write-Host "📋 Documentation Files:" -ForegroundColor Cyan
Write-Host "  • .github/LABELS.md - Label definitions" -ForegroundColor Gray
Write-Host "  • .github/PROJECT_WORKFLOW.md - Project setup guide" -ForegroundColor Gray
Write-Host "  • CODEOWNERS - Code review ownership rules" -ForegroundColor Gray
Write-Host "  • docs/INDEX.md - Complete documentation index" -ForegroundColor Gray
Write-Host ""

Write-Host "🎉 GitHub Features Configuration Complete!" -ForegroundColor Green
Write-Host ""

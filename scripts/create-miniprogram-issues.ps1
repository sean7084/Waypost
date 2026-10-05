# Create the GitHub issue backlog for the Kering Store Inspection Mini Program.
#
# Follows the repo convention (see setup-github-labels.ps1): reads a classic token
# from .env (GITHUB_CLASSIC_TOKEN) and POSTs issues via the GitHub REST API.
# An Epic is created first; each child issue references it.
#
# Most items are already implemented in this branch - their bodies are prefixed
# "[Implemented - verify & close]" so the board reflects reality after a run.
#
# Usage:  .\scripts\create-miniprogram-issues.ps1
# Dry run (print only, no API calls):  .\scripts\create-miniprogram-issues.ps1 -WhatIf

param([switch]$WhatIf)

$repoOwner = "sean7084"
$repoName  = "Waypost"
$envFile = @('.env', '.env.local') | Where-Object { Test-Path $_ } | Select-Object -First 1
$tokenLine = if ($envFile) { (Get-Content $envFile | Select-String '^GITHUB_CLASSIC_TOKEN=') } else { $null }
if (-not $tokenLine -and -not $WhatIf) { throw "GITHUB_CLASSIC_TOKEN not found in .env (or .env.local)" }
$token = if ($tokenLine) { $tokenLine.ToString().Split('=')[1] } else { '' }
$headers = @{ "Authorization" = "Bearer $token"; "Accept" = "application/vnd.github.v3+json" }
$baseUrl = "https://api.github.com/repos/$repoOwner/$repoName"

# Prerequisite: labels must exist (run setup-github-labels.ps1 first).
$epicBody = @"
Epic: WeChat mini program that replaces the Feishu questionnaire for the Kering EUS
store health-check. Backend in Waypost (inspections app), offline-first native
mini program, server-generated 'Report Per Store.xlsx' + Photo archive.

Spec: docs/MINIPROGRAM_SPEC.md  |  ADR-0011  |  Plan: WeChat_Audit_Mini_Program_task-efd.md
"@

$issues = @(
  @{ t="Phase 0: import_kering_master command (Kering -> AMS mapping, --dry-run)"; l=@("enhancement","P0 - Critical","component: import"); done=$true;
     b="Management command reading schedule.xlsx + asset_list_CN_2026.xlsx; maps Kering->Company/Division/Location/Asset + StoreInspection/InspectionDevice." },
  @{ t="Phase 0: placeholder cleaning, category normalization, rollback tracking"; l=@("enhancement","P0 - Critical","component: import"); done=$true;
     b="Clean ASSET_PLACEHOLDERS, assign placeholder-NNNNNN, normalize device categories, track via ImportRun for rollback, per-store summary." },
  @{ t="inspections app: models + migrations"; l=@("enhancement","P0 - Critical","component: inspections","migration-required"); done=$true;
     b="StoreInspection, InspectionDevice, InspectionPhoto, InspectionIssue + Kering constants (checklist, cover labels, photo prefixes)." },
  @{ t="Engineer role + can_run_inspection permissions/scoping"; l=@("enhancement","P0 - Critical","component: inspections","security"); done=$true;
     b="inspection_engineer AdminRole, User.can_run_inspection / get_assigned_inspections; engineers see only assigned inspections." },
  @{ t="SimpleJWT config (access/refresh) alongside session auth"; l=@("enhancement","P0 - Critical","component: api","security"); done=$true;
     b="settings REST_FRAMEWORK JWTAuthentication + SIMPLE_JWT; requirements djangorestframework-simplejwt." },
  @{ t="WeChatIdentity model + jscode2session helper"; l=@("enhancement","P0 - Critical","component: accounts","security","migration-required"); done=$true;
     b="accounts.WechatIdentity (appid+openid unique), accounts/wechat.py code2session; session_key never persisted." },
  @{ t="WeChat bind + seamless login endpoints"; l=@("enhancement","P0 - Critical","component: api","security"); done=$true;
     b="POST /api/v1/auth/wechat/bind/ and /login/ (+ token refresh); bound:false drives the bind screen." },
  @{ t="Inspection REST API: devices/photos/issues/signoff"; l=@("enhancement","P0 - Critical","component: api","component: inspections"); done=$true;
     b="Idempotent device upsert (client_device_uid), multipart photos, issues, signoff->submitted, PATCH store fields." },
  @{ t="Inspection REST API: checklist + report endpoints"; l=@("enhancement","P0 - Critical","component: api","component: inspections"); done=$true;
     b="GET /inspections/checklist/ (checklist + capture fields + confirmation labels); POST /inspections/{id}/report/." },
  @{ t="Photo-naming service (port EUS conventions)"; l=@("enhancement","P0 - Critical","component: inspections"); done=$true;
     b="<Category><SN>-1/-2, Rack1-1, Router-1, Switch-1, PatchPanel-1, Speedtest*, Issue-1, Other_Cash_Drawer N." },
  @{ t="Report generator: populate bundled report_template.xlsx by label"; l=@("enhancement","P0 - Critical","component: inspections"); done=$true;
     b="Populate cover_page (col D), confirmation_page (col C), asset_list by header; preserve merged cells; build Photo zip." },
  @{ t="Report transforms (monitor size, XStore link, version merge, tag autofill, status)"; l=@("enhancement","P1 - High","component: inspections"); done=$true;
     b="inspections/services/transforms.py ported from the EUS Feishu automation." },
  @{ t="Report/photo parity tests vs golden 22149 output"; l=@("needs testing","P1 - High","component: inspections"); done=$true;
     b="Content-parity tests using real 22149 rows; cell-level template population test. NOTE: byte-parity vs the golden workbook still needs the OneDrive golden file locally to finalize tuning." },
  @{ t="Mini program scaffold + request/auth/db/queue/sync utils"; l=@("enhancement","P0 - Critical","component: miniprogram"); done=$true;
     b="JWT-aware request w/ 401 refresh, offline cache, idempotent mutation queue, sync engine." },
  @{ t="Mini program: login/bind page"; l=@("enhancement","P0 - Critical","component: miniprogram"); done=$true; b="wx.login bind + seamless login." },
  @{ t="Mini program: inspections list + detail (scan, search, offline download)"; l=@("enhancement","P0 - Critical","component: miniprogram"); done=$true; b="Assigned list, progress, wx.scanCode match, search, offline cache." },
  @{ t="Mini program: device verify + photos (online path)"; l=@("enhancement","P0 - Critical","component: miniprogram"); done=$true; b="Category-driven capture fields + overall/serial photos -> queue." },
  @{ t="Mini program: offline cache download"; l=@("enhancement","P1 - High","component: miniprogram"); done=$true; b="utils/db.js caches inspection + checklist." },
  @{ t="Mini program: offline queue + idempotency keys"; l=@("enhancement","P1 - High","component: miniprogram"); done=$true; b="utils/queue.js dedupeKey + client uids." },
  @{ t="Mini program: sync engine + status page"; l=@("enhancement","P1 - High","component: miniprogram"); done=$true; b="utils/sync.js FIFO flush, offline-safe; pages/sync." },
  @{ t="Mini program: rack-network / issues / confirmation / signoff / report pages"; l=@("enhancement","P1 - High","component: miniprogram"); done=$true; b="P1 workflow pages incl. signature canvas + report download." },
  @{ t="Env + secrets config + production fail-fast guard"; l=@("deployment","security","P0 - Critical"); done=$true;
     b="WECHAT_MINI_APPID/APPSECRET, JWT_SIGNING_KEY in .env.example; settings fail fast when DEBUG=False." },
  @{ t="Production HTTPS domain + WeChat legal-domain whitelist"; l=@("deployment","P1 - High"); done=$false;
     b="EXTERNAL: register API host in MP console (request/uploadFile/downloadFile); ICP-filed HTTPS domain." },
  @{ t="Docs: MINIPROGRAM_SPEC + ADR-0011 + API_GUIDE + INDEX"; l=@("documentation","P1 - High"); done=$true; b="Spec, ADR, JWT/inspection API docs, index entries." },
  @{ t="Labels: component api/inspections/miniprogram/import"; l=@("documentation","P2 - Medium"); done=$true; b="LABELS.md + setup-github-labels.ps1." },
  @{ t="CI: path-filtered backend + mini program workflows"; l=@("enhancement","P2 - Medium","component: miniprogram"); done=$true;
     b=".github/workflows/backend-ci.yml + miniprogram-ci.yml. OPTIONAL follow-up: miniprogram-ci preview/upload + eslint config." },
  @{ t="Historical photo reuse service + command"; l=@("enhancement","P2 - Medium","component: inspections"); done=$true;
     b="inspections/services/historical_photos.py + reuse_historical_photos command (DB-native SN match)." },
  @{ t="WeChat review/release checklist + versioning"; l=@("deployment","P2 - Medium","component: miniprogram"); done=$true; b="miniprogram/README.md release checklist." }
)

function New-Issue($title, $body, $labels) {
  $payload = @{ title=$title; body=$body; labels=$labels } | ConvertTo-Json
  if ($WhatIf) { Write-Host "[whatif] $title  ($($labels -join ', '))" -ForegroundColor DarkGray; return $null }
  return Invoke-RestMethod -Uri "$baseUrl/issues" -Method Post -Headers $headers -Body $payload -ContentType "application/json"
}

Write-Host "Creating epic..." -ForegroundColor Cyan
$epic = New-Issue "Epic: Kering Store Inspection Mini Program" $epicBody @("enhancement","P0 - Critical")
$epicRef = if ($epic) { "Epic: #$($epic.number)`n`n" } else { "" }

$created = 0; $failed = 0
foreach ($item in $issues) {
  $prefix = if ($item.done) { "[Implemented - verify & close] " } else { "" }
  $body = "$epicRef$($item.b)"
  try {
    $res = New-Issue "$prefix$($item.t)" $body $item.l
    if ($res -or $WhatIf) { Write-Host "[ok] $($item.t)" -ForegroundColor Green; $created++ }
  } catch {
    Write-Host "[fail] $($item.t): $($_.Exception.Message)" -ForegroundColor Red; $failed++
  }
}

Write-Host "`n=== Summary ===" -ForegroundColor Cyan
Write-Host "Created/planned: $created  Failed: $failed" -ForegroundColor Green

<#
.SYNOPSIS
    Deploy a branch (default: origin/main) to the production ECS for verification.

.DESCRIPTION
    This is the *fast* path. It is NOT a release.

    A release is a `v*` tag: pushing one runs .github/workflows/deploy-ecs.yml,
    which deploys through this same host-side script and records result=OK in
    /srv/waypost/backups/deploy.log. See docs/RELEASE_PROCEDURE.md.

    This script deploys an arbitrary ref (normally origin/main) so you can look at
    merged work on the real host before deciding to tag it. The deploy is recorded
    with kind=untagged-ref and result=DEV_OK, and the image is built as
    waypost-app:dev-<ref>, so a verification deploy never masquerades as a release
    and never overwrites a release image tag.

    READ THIS BEFORE RUNNING: there is only one database on this host. `main` may
    contain migrations, and the container entrypoint applies them to the
    production database on the way up. The script takes a pg_dump checkpoint first
    (ci-deploy.sh step 2) and shows you the migrations it is about to apply, but a
    one-way-door migration cannot be undone by rolling the image back. If main
    contains one of those (docs/RELEASE_PROCEDURE.md §9.2 lists the known ones),
    tag a release instead of using this script, and follow §9.

    What it does:
      1. fetches origin locally and shows you the commits and migrations that will
         go out, then asks for confirmation (-Force to skip);
      2. installs the ci-deploy.sh *from the ref being deployed* onto the host, so
         the runner always matches the code it is deploying (it lives outside the
         git checkout because it rewrites that checkout);
      3. runs it with --allow-untagged, which builds, re-creates the containers,
         waits for /healthz/ on the loopback interface and then through Nginx, and
         rolls the image back automatically if the app never becomes healthy.

.PARAMETER Ref
    Git ref to deploy. Default origin/main. A branch, remote-tracking branch or
    SHA all work; a tag works too but then you should just push the tag and let CI
    do it, so that the deploy is attributed to the release.

.PARAMETER Force
    Skip the confirmation prompt. For scripts and CI, not for routine use.

.PARAMETER SkipPublicGate
    Do not fail when the app is healthy on the loopback interface but unreachable
    through Nginx. Useful when you are deliberately working on the edge and the
    public URL is expected to be down; the app itself is still health-gated.

.EXAMPLE
    .\scripts\deploy-dev-to-ecs.ps1
    Deploys origin/main after showing what changed and asking for confirmation.

.EXAMPLE
    .\scripts\deploy-dev-to-ecs.ps1 -Ref origin/feat/some-branch -Force

.NOTES
    Requires an SSH key that the `deploy` account accepts. The one installed in
    /home/deploy/.ssh/authorized_keys (comment `operator-waypost-deploy`) is the
    SAME ed25519 key you already use for the `sean` account, so by default this
    script passes no -i and lets ssh use the agent / ~/.ssh/id_ed25519. Pass
    -Identity only if you keep that key somewhere unusual.

    The separate CI deploy key (`waypost-ci-deploy`, `restrict`ed) lives only in
    GitHub Secrets and cannot be used from a workstation.

    The `deploy` user has no sudo and cannot see the Helpdesk containers: it runs
    its own rootless Docker daemon. Nothing this script does can touch Helpdesk.
#>
[CmdletBinding()]
param(
    [string]$Ref = 'origin/main',
    [string]$TargetHost = '47.120.31.129',
    [int]$Port = 49153,
    [string]$User = 'deploy',
    [string]$Identity = '',
    [string]$AppDir = '/srv/waypost/app',
    [switch]$Force,
    [switch]$SkipPublicGate
)

$ErrorActionPreference = 'Stop'

# Resolve the repository root from this script's location, so it can be run from
# anywhere (the Helpdesk convention is the same).
$repoRoot = Split-Path -Parent $PSScriptRoot

# BatchMode=yes: never hang on a password prompt. If the key is not accepted we
# want an immediate, legible failure rather than an interactive stall.
# -n on the query calls: without it ssh inherits stdin and swallows it, which
# would leave nothing for the confirmation prompt below to read.
$sshBase = @('-p', "$Port", '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=accept-new')
if ($Identity) {
    if (-not (Test-Path $Identity)) { throw "SSH private key not found: $Identity" }
    $sshBase += @('-i', $Identity)
}
$sshTarget = "$User@$TargetHost"

function Write-Step { param([string]$Message) Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Warn2 { param([string]$Message) Write-Host "!!  $Message" -ForegroundColor Yellow }

function Invoke-Ssh {
    param([Parameter(Mandatory)][string]$RemoteCommand)
    & ssh -n @sshBase -o ConnectTimeout=20 $sshTarget $RemoteCommand
    if ($LASTEXITCODE -ne 0) { throw "ssh command failed (exit $LASTEXITCODE): $RemoteCommand" }
}

# --- pre-flight --------------------------------------------------------------
if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) { throw 'OpenSSH client (ssh) not found on PATH.' }
try {
    $whoami = (Invoke-Ssh 'id -un') | Select-Object -First 1
} catch {
    throw "Cannot reach $sshTarget on port $Port with the current SSH identities.`n$($_.Exception.Message)`nThe deploy account authorises the key commented 'operator-waypost-deploy'; see docs/DEPLOYMENT.md §2.8."
}
if ($whoami -ne $User) { throw "Authenticated as '$whoami', expected '$User'." }

Push-Location $repoRoot
try {
    Write-Step "Fetching origin in $repoRoot"
    # github.com is intermittently unreachable from this network (observed as a
    # 135s connect timeout with the next attempt succeeding). The host-side runner
    # already retries for the same reason; a single attempt here would make the
    # script flaky in exactly the way that is most annoying to diagnose.
    $fetched = $false
    foreach ($attempt in 1..3) {
        git fetch --tags --force origin --quiet
        if ($LASTEXITCODE -eq 0) { $fetched = $true; break }
        Write-Warn2 "git fetch attempt $attempt/3 failed; retrying in 10s"
        Start-Sleep -Seconds 10
    }
    if (-not $fetched) { throw 'git fetch failed after 3 attempts - is github.com reachable?' }

    if (-not (git rev-parse --verify --quiet "$Ref^{commit}")) {
        throw "Ref '$Ref' does not resolve to a commit in this repository."
    }
    $targetSha = (git rev-parse --short "$Ref^{commit}")

    Write-Step "Asking the host what it is running now"
    $currentSha = (Invoke-Ssh "git -C $AppDir rev-parse --short HEAD 2>/dev/null || echo unknown") | Select-Object -First 1
    $currentRef = (Invoke-Ssh "cd $AppDir && (git describe --tags 2>/dev/null || git rev-parse --abbrev-ref HEAD 2>/dev/null || echo detached) | head -1") | Select-Object -First 1
    Write-Host "    host is at: $currentSha ($currentRef)"
    Write-Host "    deploying : $targetSha ($Ref)"

    if ($currentSha -eq $targetSha) {
        Write-Warn2 "The host is already at $targetSha. Re-running still rebuilds the image (picking up a newer base image) and re-creates the containers."
    } else {
        Write-Step 'Commits that will go out'
        git log --oneline --no-decorate "$currentSha..$Ref" 2>$null | ForEach-Object { Write-Host "    $_" }

        $migrations = git diff --name-only "$currentSha..$Ref" -- '*/migrations/*.py' 2>$null
        if ($migrations) {
            Write-Warn2 'This ref contains migrations. They WILL be applied to the production database:'
            $migrations | ForEach-Object { Write-Host "    $_" -ForegroundColor Yellow }
            Write-Host '    A pg_dump checkpoint is taken automatically before the deploy, but a'
            Write-Host '    one-way-door migration cannot be undone by rolling the image back.'
            Write-Host '    Cross-check docs/RELEASE_PROCEDURE.md §9.2 before continuing.'
        } else {
            Write-Host '    (no migrations in this range)'
        }
    }

    if (-not $Force) {
        # Fail CLOSED. [string] is load-bearing: Read-Host returns $null when stdin
        # has been consumed or is not a console, and `-notmatch` against a
        # non-string left operand does not behave like a boolean test - it filters,
        # which made an earlier version of this gate proceed on an empty answer.
        # Casting first means anything that is not literally y/yes aborts.
        Write-Host ''
        $answer = [string](Read-Host "Deploy $Ref to https://ams.istore-tech.cn/ now? [y/N]")
        $normalized = $answer.Trim().ToLowerInvariant()
        if ($normalized -ne 'y' -and $normalized -ne 'yes') {
            Write-Host "Aborted (answer: '$answer'), nothing was changed."
            return
        }
    }
} finally {
    Pop-Location
}

# --- deploy ------------------------------------------------------------------
# The runner is re-installed from the ref being deployed, so host-side logic and
# application code cannot drift apart. It lives in /srv/waypost/bin (outside the
# checkout) because ci-deploy.sh does `git checkout`/`git clean` inside it.
#
# The capability check is what stops a confusing outcome: a ref whose runner
# predates --allow-untagged would be installed, then refuse the flag, then roll
# back - looking like a deploy failure rather than a version mismatch.
$envPrefix = if ($SkipPublicGate) { 'SKIP_PUBLIC_GATE=1 ' } else { '' }
$remote = @"
set -eu
cd $AppDir
git fetch --tags --force origin --quiet
rm -f /tmp/.ci-deploy.sh.next
# `${Ref} is braced on purpose: in a double-quoted here-string PowerShell reads
# `$Ref:docker as a drive-qualified variable (like `$env:PATH) and expands it to
# nothing, which sends git the path '/bin/ci-deploy.sh' instead.
if ! git show '${Ref}:docker/bin/ci-deploy.sh' > /tmp/.ci-deploy.sh.next; then
    echo 'FATAL: ${Ref} has no docker/bin/ci-deploy.sh' >&2
    rm -f /tmp/.ci-deploy.sh.next
    exit 1
fi
if ! grep -q 'allow-untagged' /tmp/.ci-deploy.sh.next; then
    echo 'FATAL: the ci-deploy.sh at ${Ref} does not support --allow-untagged.' >&2
    echo '       Deploy a ref that does (this script''s own branch or later), or' >&2
    echo '       cut a tag and let the CD pipeline deploy it.' >&2
    rm -f /tmp/.ci-deploy.sh.next
    exit 1
fi
install -m 0755 /tmp/.ci-deploy.sh.next /srv/waypost/bin/ci-deploy.sh
rm -f /tmp/.ci-deploy.sh.next
${envPrefix}/srv/waypost/bin/ci-deploy.sh '${Ref}' --allow-untagged
"@
# A here-string inherits this file's CRLF line endings, and bash rejects them
# (`set -eu\r` -> "set: -\r: invalid option"). Normalize to LF before sending.
$remote = $remote -replace "`r`n", "`n" -replace "`r", "`n"

Write-Step "Deploying $Ref (this rebuilds the image; a few minutes on a 2 vCPU host)"
# Deliberately not captured: the deploy output streams live, and its exit code is
# what tells us whether the health gate passed.
& ssh @sshBase $sshTarget $remote
$deployExit = $LASTEXITCODE

# --- report ------------------------------------------------------------------
Write-Step 'Result'
try {
    Invoke-Ssh 'docker ps --format "{{.Names}}\t{{.Status}}" | grep waypost_ || true' | ForEach-Object { Write-Host "    $_" }
    Write-Host '    last 5 deploy.log entries:'
    Invoke-Ssh 'tail -5 /srv/waypost/backups/deploy.log 2>/dev/null || true' | ForEach-Object { Write-Host "      $_" }
} catch {
    Write-Warn2 "Could not read the post-deploy status: $($_.Exception.Message)"
}

if ($deployExit -ne 0) {
    Write-Warn2 "The remote deploy step exited $deployExit. A pre-flight failure (above) means nothing was changed; a health-gate failure means the previous image has already been restored. Either way, read the output above and /srv/waypost/backups/deploy.log."
    exit $deployExit
}
Write-Host "==> $Ref is live at https://ams.istore-tech.cn/ (recorded as a DEV deploy, not a release)" -ForegroundColor Green
Write-Host '    To make it a release, tag it instead: see docs/RELEASE_PROCEDURE.md §5-§6.'

import {
  Callout,
  Divider,
  H1,
  type MetricItem,
  MetricsGrid,
  ReferencePanel,
  type ReferenceItem,
  ReportSection,
  ReportShell,
  Stack,
  Table,
  Tag,
  Text,
} from "qoder/canvas";

const headlineMetrics: MetricItem[] = [
  { label: "Tests passing", value: "79", tone: "success", description: "1 env-gated skip" },
  { label: "GitHub issues live", value: "28", description: "Epic #30 + 27 children" },
  { label: "Stores imported", value: "126", description: "real Kering dataset" },
  { label: "Devices imported", value: "3,550", description: "ImportRun #8 rollbackable" },
];

const gateRows: string[][] = [
  ["System check", "manage.py check", "no issues (0 silenced)"],
  ["Migrations", "makemigrations --check", "No changes detected"],
  ["Test suite", "manage.py test", "Ran 79 tests · OK (skipped=1)"],
  ["Mini program lint", "npm run lint (eslint 9)", "exit 0"],
  ["Workflow YAML", "js-yaml (backend + miniprogram CI)", "exit 0"],
  ["Real-data import", "import_kering_master", "126 inspections · 3,550 devices"],
  ["Issue backlog", "create-miniprogram-issues.ps1", "28 created · 0 failed"],
];

type Coverage = {
  area: string;
  status: string;
  statusTone: "success" | "warning";
  evidence: string;
  rowTone: "default" | "warning";
};

const coverage: Coverage[] = [
  { area: "Phase 0 — Kering import", status: "Done", statusTone: "success", evidence: "import_kering_master.py; executed on real data", rowTone: "default" },
  { area: "Backend A — inspections app", status: "Done", statusTone: "success", evidence: "models, constants, admin, migration 0001", rowTone: "default" },
  { area: "Backend B — WeChat auth + JWT", status: "Done", statusTone: "success", evidence: "wechat.py, WeChatIdentity, bind/login/refresh", rowTone: "default" },
  { area: "Backend C — REST API", status: "Done", statusTone: "success", evidence: "all endpoints incl. devices/{uid} upsert", rowTone: "default" },
  { area: "Backend D — report + photos", status: "Done", statusTone: "success", evidence: "services + golden-parity test", rowTone: "default" },
  { area: "Client — 10 pages offline-first", status: "Done", statusTone: "success", evidence: "scan / search / new-device / signature / share", rowTone: "default" },
  { area: "Infra — server-side upload caps", status: "Done", statusTone: "success", evidence: "_reject_oversized_uploads + 2 tests (this session)", rowTone: "default" },
  { area: "Labels + CI", status: "Done", statusTone: "success", evidence: "4 labels live; path-filtered workflows", rowTone: "default" },
  { area: "Documentation", status: "Done", statusTone: "success", evidence: "spec, ADR-0011, API_GUIDE, + runbook", rowTone: "default" },
  { area: "Issue backlog", status: "Live", statusTone: "success", evidence: "Epic #30 + children #31–#58 on GitHub", rowTone: "default" },
  { area: "Infra — AppID / HTTPS / whitelist", status: "Operator", statusTone: "warning", evidence: "runbook + issue #53 (external)", rowTone: "warning" },
];

const changedRows: string[][] = [
  ["api/inspection_views.py", "Added _reject_oversized_uploads cap on 4 write endpoints"],
  ["api/tests_miniprogram.py", "+2 upload-cap regression tests (18 total)"],
  ["docs/DEPLOYMENT_MINIPROGRAM.md", "New deployment runbook (264 lines)"],
  ["docs/INDEX.md", "Linked the runbook in 2 places"],
  [".github/workflows/miniprogram-ci.yml", "Gated miniprogram-ci preview job (secret-driven)"],
  [".gitignore", "Ignore ci_private.key + preview-qr.jpg"],
  ["miniprogram/README.md", "Fixed stale Status/Structure (now 10 pages)"],
  ["GitHub remote", "4 component labels + Epic #30 + 27 issues"],
];

const operatorRows: string[][] = [
  ["Register MP AppID / AppSecret", "Needs your WeChat business account", "runbook §1"],
  ["HTTPS + ICP domain + legal-domain whitelist", "Needs production server / domain", "runbook §3 · issue #53"],
  ["Nginx media serving for reports / photos", "Needs production server", "runbook §4"],
  ["On-device E2E in WeChat DevTools", "Needs WeChat IDE + real AppID", "runbook §9"],
  ["Submit for WeChat review + release", "Needs console + credentials", "runbook §10"],
];

const references: ReferenceItem[] = [
  {
    id: "epic",
    kind: "issue",
    label: "Epic #30 — Kering Store Inspection Mini Program",
    description: "Parent of the 27-issue backlog created this session.",
    href: "https://github.com/sean7084/HengjiAMS1/issues/30",
    meta: "live",
  },
  {
    id: "runbook",
    kind: "runbook",
    label: "docs/DEPLOYMENT_MINIPROGRAM.md",
    description: "Console registration, legal domains, Nginx, CI, release.",
    meta: "new",
  },
  {
    id: "spec",
    kind: "doc",
    label: "docs/MINIPROGRAM_SPEC.md",
    description: "Personas, screens, offline/sync contract, API map.",
  },
  {
    id: "adr",
    kind: "doc",
    label: "ADR-0011",
    description: "Monorepo, WeChat JWT bound to staff, Kering-specific app.",
  },
];

export default function WeChatMiniProgramCompletion() {
  return (
    <ReportShell width="wide" ariaLabel="WeChat mini program spec completion report">
      <Stack gap="sectionCompact">
        <header>
          <Stack gap="component">
            <H1>WeChat Audit Mini Program — Spec Completion</H1>
            <Text tone="secondary">
              Kering EUS store device-inspection mini program (replaces the Feishu questionnaire).
              Backend in HengjiAMS1 · offline-first native client · server-generated Report Per Store.xlsx.
            </Text>
            <MetricsGrid variant="header" columns={4} items={headlineMetrics} />
          </Stack>
        </header>

        <ReportSection title="Outcome" divided>
          <Stack gap="component">
            <Callout tone="success" title="Spec fully implemented and verified">
              Every implementable requirement of WeChat_Audit_Mini_Program_task-efd.md is complete and
              backed by current-state evidence (gates below). The three approved follow-ups — live GitHub
              issues, a deployment runbook, and the gated CI preview job — are done, and the last code gap
              (server-side upload caps) was closed this session. Only operator-side provisioning remains.
            </Callout>
            <Text tone="secondary" size="small">
              The WeChat mini program is not browser-reachable (it renders only in WeChat DevTools with a
              registered AppID), so verification relies on the Django test suite, live HTTP API tests, the
              persisted real-data import, and the GitHub API — not a browser screenshot.
            </Text>
          </Stack>
        </ReportSection>

        <ReportSection title="Verification gates" description="All run against the current worktree this session." divided>
          <Table
            headers={["Gate", "Command", "Result"]}
            rows={gateRows}
            rowTone="success"
            density="compact"
          />
        </ReportSection>

        <ReportSection title="Spec coverage" divided>
          <Table
            headers={["Spec area", "Status", "Evidence"]}
            rows={coverage.map((c) => [c.area, <Tag tone={c.statusTone}>{c.status}</Tag>, c.evidence])}
            rowTone={coverage.map((c) => c.rowTone)}
            density="compact"
          />
        </ReportSection>

        <ReportSection title="Changed this session" description="On top of the previously implemented feature." divided>
          <Table headers={["File / target", "Change"]} rows={changedRows} density="compact" />
        </ReportSection>

        <ReportSection title="Remaining operator prerequisites" divided>
          <Stack gap="component">
            <Callout tone="warning" title="External — cannot be performed in this environment">
              These require your WeChat business account, production server, and ICP-filed domain. All are
              documented step-by-step in the new runbook and tracked as GitHub issue #53.
            </Callout>
            <Table
              headers={["Task", "Why it is external", "Where"]}
              rows={operatorRows}
              rowTone="warning"
              density="compact"
            />
          </Stack>
        </ReportSection>

        <ReportSection title="Key artifacts">
          <ReferencePanel items={references} columns={2} />
        </ReportSection>

        <Divider />
        <Text tone="tertiary" size="small">
          Generated Sept 13, 2026 · HengjiAMS1 monorepo · branch docs/14-backup-restore (work uncommitted, awaiting your review).
        </Text>
      </Stack>
    </ReportShell>
  );
}

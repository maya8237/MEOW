export const REPO_URL = "https://github.com/maya8237/MEOW";
export const sourceUrl = (path: string) => `${REPO_URL}/blob/main/${path}`;

export const runSkill = [
  "run",
  "Build something",
  "Plan, build, review, and deliver a change. A worktree run pushes, opens an MR, then verifies its CI pipeline.",
  "Add retry with backoff to the HTTP client",
] as const;

export const skillGroups = [
  {
    title: "Set up & make it yours",
    skills: [
      [
        "onboard",
        "Set up your project",
        "Checks your project, writes the MEOW config, and offers optional add-ons: test commands (suggested from what it finds, and only added if you approve), a test-writing tester, Jira, GitHub or GitLab, scheduling, and the lint hook (lints each file as Claude edits it). Run once per project; rerun to add or change something.",
        "",
      ],
      [
        "customize",
        "Make MEOW work your way",
        "Change skills, agent roles, or workflows.",
        "Create a skill for our coding conventions",
      ],
    ],
  },
  {
    title: "Plan, review, and clean up",
    skills: [
      [
        "plan",
        "Think it through first",
        "Create a plan before implementation.",
        "Add rate limiting to the public API",
      ],
      [
        "review",
        "Review your code",
        "Review existing work and fix findings, or review a GitHub PR or GitLab MR from its link (report only).",
        "",
      ],
      [
        "lint",
        "Clean up lint findings",
        "Find and fix issues using your project’s linter.",
        "",
      ],
    ],
  },
  {
    title: "When you need more",
    skills: [
      [
        "integrate-ci-review",
        "Review pull requests in CI",
        "Add a code review job to GitHub Actions or GitLab CI. It reviews proposed changes against the branch you’re merging into and saves a PASS/FAIL report as a downloadable CI artifact. You can also enable inline MR comments. The job reports findings without applying code fixes.",
        "",
      ],
      [
        "schedule",
        "Run work later",
        "Schedule a task for a time you choose.",
        'run "Update the API docs" at 09:00',
      ],
    ],
  },
];

export const reviewExamples = [
  "/meow:review MR 42",
  "/meow:review https://gitlab.com/your-group/your-repo/-/merge_requests/42",
];

export const runExamples = [
  {
    title: "A simple request",
    prompt: "/meow:run Add a dark mode toggle to the settings page",
  },
  {
    title: "A detailed request with branches",
    prompt:
      "/meow:run Add a password reset flow. Users request a reset link by email, the link expires after 30 minutes and works once, and the new password must meet the existing password rules. Show the same confirmation message whether or not the email exists. Add tests for expired, reused, and invalid links. Start from the release branch and open the MR into dev.",
  },
  {
    title: "Approve the plan first",
    prompt:
      "/meow:run Add rate limiting to the public API: 100 requests per minute per API key, with a clear error when the limit is hit. Show me the plan and wait for my approval before you build anything.",
  },
  {
    title: "From a Jira issue",
    prompt: "/meow:run PROJ-123",
  },
  {
    title: "A quick, small change",
    prompt:
      "/meow:run Rename the Save button to Apply on the profile form. Use the quick workflow.",
  },
  {
    title: "Use a custom workflow",
    prompt:
      "/meow:run Add a shipped_at column to orders and backfill existing rows. Use our migration-review workflow.",
  },
  {
    title: "Review and fix existing code",
    prompt:
      "/meow:run Audit the checkout API for security problems and fix what you find. Use the review-and-fix workflow.",
  },
  {
    title: "Fix a bug",
    prompt:
      "/meow:run Fix duplicate submissions when a user double-clicks Save on the profile form. Reproduce the bug, disable Save while the request is pending, and allow retry after a failure. Add regression tests for double-clicks and failed requests.",
  },
  {
    title: "Coordinate multiple features",
    prompt:
      "/meow:run Add two independently scoped features: email/password sign-in with sign-out and protected pages, and an audit log for account changes. Keep secrets out of logs and test access permissions. Use full sprint orchestration and deliver one reviewed branch and MR.",
  },
];

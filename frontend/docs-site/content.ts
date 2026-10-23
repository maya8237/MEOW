export const REPO_URL = "https://github.com/maya8237/MEOW";
export const sourceUrl = (path: string) => `${REPO_URL}/blob/main/${path}`;

export const skillGroups = [
  {
    title: "Set up & make it yours",
    skills: [
      [
        "onboard",
        "Set up your project",
        "Run once per project to configure its settings, skills, integrations, and optional features. Later tasks reuse that setup. Run onboarding again when you want to add or change something.",
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
    title: "Your everyday tools",
    skills: [
      [
        "run",
        "Build something",
        "Plan, build, check, and review a change. Standard runs commit and push; you open the PR/MR yourself. Full sprint orchestration also opens one PR (GitHub) or MR (GitLab) automatically after the combined work passes review, unless disabled.",
        "Add CSV export",
      ],
      [
        "plan",
        "Think it through first",
        "Create a plan before implementation.",
        "Add CSV export",
      ],
      [
        "review",
        "Review your code",
        "Review existing work and fix findings.",
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
        "Add a code review job to GitHub Actions or GitLab CI. It reviews proposed changes against the branch you’re merging into and saves a PASS/FAIL report as a downloadable CI artifact. You can also enable inline PR/MR comments. The job reports findings without applying code fixes.",
        "",
      ],
      [
        "schedule",
        "Run work later",
        "Schedule a task for a time you choose.",
        'run "Add CSV export" at 09:00',
      ],
    ],
  },
];

export const runExamples = [
  {
    title: "Build a feature",
    prompt:
      "/meow:run Add CSV export to the Orders page. Export only the currently filtered rows, include column headers, and escape commas and quotes. Show an empty state when there are no results. Add tests for filters, empty results, and quoted values.",
  },
  {
    title: "Fix a bug",
    prompt:
      "/meow:run Fix duplicate submissions when a user double-clicks Save on the profile form. Reproduce the bug, disable Save while the request is pending, and allow retry after a failure. Add regression tests for double-clicks and failed requests.",
  },
  {
    title: "Coordinate multiple features",
    prompt:
      "/meow:run Add two independently scoped features: email/password sign-in with sign-out and protected pages, and an audit log for account changes. Keep secrets out of logs and test access permissions. Use full sprint orchestration and deliver one reviewed branch and PR.",
  },
];

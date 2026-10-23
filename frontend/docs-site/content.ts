export const REPO_URL = "https://github.com/maya8237/MEOW";
export const sourceUrl = (path: string) => `${REPO_URL}/blob/main/${path}`;

export const runSkill = [
  "run",
  "Build something",
  "Plan, build, review, and deliver a change. Worktree runs push, open a PR (GitHub) or MR (GitLab), then verify its CI pipeline.",
  "Fix the login redirect loop",
] as const;

export const skillGroups = [
  {
    title: "Set up & make it yours",
    skills: [
      [
        "onboard",
        "Set up your project",
        "Checks your project, writes the MEOW config, and offers optional add-ons such as Jira, GitHub or GitLab, scheduling, and the lint hook. Run once per project; rerun to add or change something.",
        "",
      ],
      [
        "customize",
        "Make MEOW work your way",
        "Change skills, agent roles, or workflows. See what each user, project, and local scope customizes with the meow ui command.",
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
        'run "Update the API docs" at 09:00',
      ],
    ],
  },
];

export const runExamples = [
  {
    title: "A simple request",
    prompt: "/meow:run Add a dark mode toggle to the settings page",
  },
  {
    title: "A detailed request that opens a PR/MR",
    prompt:
      "/meow:run Add a password reset flow. Users request a reset link by email, the link expires after 30 minutes and works once, and the new password must meet the existing password rules. Show the same confirmation message whether or not the email exists. Add tests for expired, reused, and invalid links. Start from the release branch and open the PR into dev.",
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

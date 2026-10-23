export const REPO_URL = "https://github.com/maya8237/MEOW";
export const sourceUrl = (path: string) => `${REPO_URL}/blob/main/${path}`;

export const skillGroups = [
  {
    title: "Set up & make it yours",
    skills: [
      [
        "onboard",
        "Set up your project",
        "Choose integrations and optional features.",
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
        "Plan, build, check, and review a change.",
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
        "schedule",
        "Run work later",
        "Schedule a task for a time you choose.",
        'run "Add CSV export" at 09:00',
      ],
      [
        "integrate-ci-review",
        "Add review to CI",
        "Set up MEOW review in GitHub Actions or GitLab CI.",
        "",
      ],
      [
        "migration",
        "Update an older project",
        "Move a legacy MEOW layout to the current format.",
        "",
      ],
    ],
  },
];

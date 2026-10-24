import { useState } from "react";
import { BookOpen, Shapes, SlidersHorizontal } from "lucide-react";
import CopyButton from "@/components/CopyButton";
import logo from "../../src/meow/ui/logo.png";
import {
  reviewExamples,
  runExamples,
  runSkill,
  skillGroups,
  sourceUrl,
} from "./content";

export type Page = "start" | "skills" | "customize";

function Command({ text, prefix }: { text: string; prefix?: string }) {
  const [error, setError] = useState("");
  return (
    <div className="guide-command-wrap">
      <div className="guide-command">
        <code>
          {prefix && <span className="guide-path-root">{prefix}</span>}
          {text}
        </code>
        <CopyButton text={text} onError={setError} />
      </div>
      {error && <p role="alert">{error}</p>}
    </div>
  );
}

function Install() {
  const [shell, setShell] = useState("powershell");
  return (
    <>
      <div className="guide-install-heading">
        <h3>Install MEOW</h3>
        <label>
          <span className="sr-only">Your terminal</span>
          <select
            value={shell}
            onChange={(event) => setShell(event.target.value)}
          >
            <option value="powershell">PowerShell</option>
            <option value="bash">Bash</option>
          </select>
        </label>
      </div>
      <p>Run this in your terminal, then restart Claude Code.</p>
      <Command
        key={shell}
        text={
          shell === "powershell"
            ? "irm https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/install.ps1 | iex"
            : "curl -fsSL https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/install.sh | sh"
        }
      />
      <p className="guide-note">
        You’ll need Python 3.12+ and Claude Code with authentication set up.
      </p>
    </>
  );
}

function Start() {
  return (
    <>
      <section className="guide-hero">
        <div>
          <h1>
            Describe a feature.
            <br />
            Get a finished MR.
          </h1>
          <p>
            MEOW plans, builds, tests, and reviews complete features, then opens
            an MR for you. One request is enough.
          </p>
          <div className="guide-actions">
            <a className="guide-primary" href="#setup">
              Set up MEOW
            </a>
            <a className="guide-inline-link" href="#skills">
              Explore the skills
            </a>
          </div>
        </div>
        <img src={logo} alt="MEOW pixel cat" width="200" height="200" />
      </section>
      <section id="setup" className="guide-setup" aria-labelledby="setup-title">
        <h2 id="setup-title">Your first steps</h2>
        <ol className="guide-steps">
          <li>
            <Install />
          </li>
          <li>
            <h3>Let MEOW get to know your project</h3>
            <p>Open your project in Claude Code and run:</p>
            <Command text="/meow:onboard" />
            <p>MEOW checks your project, then sets up:</p>
            <ul>
              <li>Your lint command, so findings get fixed automatically.</li>
              <li>The branch your MRs target.</li>
            </ul>
            <p>And offers, if you want them:</p>
            <ul>
              <li>
                Your test commands. MEOW suggests the ones it finds, checks they
                run, and adds them only if you approve.
              </li>
              <li>A tester that can write new tests (off by default).</li>
              <li>Jira, GitHub, or GitLab, to read issues and open MRs.</li>
              <li>Scheduled runs.</li>
              <li>
                A lint hook for Claude Code. It lints each file as Claude edits
                it, and lets MEOW skip re-running lint that already passed.
              </li>
            </ul>
            <p className="guide-note">
              Run once per project. Every add-on asks first; run onboarding
              again to add or change something.
            </p>
          </li>
          <li>
            <h3>Give MEOW a feature or task</h3>
            <Command text="/meow:run Add pagination to the users API endpoint" />
            <p>Or add detail, such as behavior and checks:</p>
            <Command text="/meow:run Add cursor-based pagination to GET /users. Accept limit (default 20, max 100) and cursor query parameters, return next_cursor in the response, and reject invalid cursors with a 400. Update the OpenAPI spec and add tests for the first page, last page, and invalid cursors." />
            <p className="guide-note">
              MEOW works in its own worktree and opens an MR when checks pass.
            </p>
          </li>
        </ol>
      </section>
      <nav className="guide-next" aria-label="Keep exploring">
        <a href="#skills">
          <Shapes aria-hidden="true" />
          <span>
            <strong>Find your next skill</strong>
            <small>Plan, build, review, and more.</small>
          </span>
        </a>
        <a href="#customize">
          <SlidersHorizontal aria-hidden="true" />
          <span>
            <strong>Make it yours</strong>
            <small>Your skills, agents, and workflows.</small>
          </span>
        </a>
      </nav>
    </>
  );
}

function Skills() {
  const [, runTitle, runDescription, runExample] = runSkill;
  return (
    <>
      <div className="guide-page-heading">
        <h1>What would you like to do?</h1>
        <p>Type a skill in Claude Code, then describe what you need.</p>
      </div>
      <section className="guide-run" aria-labelledby="run-title">
        <h2 id="run-title">/meow:run</h2>
        <p className="guide-run-lead">
          {runTitle}. {runDescription}
        </p>
        <Command text={`/meow:run ${runExample}`} />
        <h3>Detailed prompt examples</h3>
        {runExamples.map(({ title, prompt }) => (
          <section key={title}>
            <h4>{title}</h4>
            <Command text={prompt} />
          </section>
        ))}
        <p className="guide-note">
          Every request, simple or detailed, runs in its own worktree and, once
          checks pass, opens an MR into your configured target (set during
          onboarding) and verifies its pipeline. MR creation and pipeline checks
          require your provider connection; if they fail, MEOW reports the
          reason and available links.{" "}
          <a href={sourceUrl("docs/PIPELINE.md")}>MR & pipeline guide</a>
        </p>
        <p className="guide-note">
          Optional: to change where work starts or where the MR goes, name the
          branches in your prompt. To use a workflow, name it in your prompt.
          Ask MEOW to test the change if you want the tester to run.
        </p>
        <a className="guide-detail" href={sourceUrl("skills/run/SKILL.md")}>
          Full skill instructions on GitHub
        </a>
      </section>
      <h2 className="guide-all-heading">All skills</h2>
      {skillGroups.map(({ title, skills }) => (
        <section className="guide-skill-group" key={title}>
          <h2>{title}</h2>
          <div className="guide-skills">
            {skills.map(([name, title, description, example]) => (
              <section className="guide-skill" key={name}>
                <h3>{title}</h3>
                <p>{description}</p>
                <Command
                  text={`/meow:${name}${example ? ` ${example}` : ""}`}
                />
                {name === "review" &&
                  reviewExamples.map((text) => (
                    <Command key={text} text={text} />
                  ))}
                <div className="guide-skill-links">
                  {name === "customize" && (
                    <a
                      className="guide-detail guide-detail-main"
                      href="#customize"
                    >
                      See the Customize page
                    </a>
                  )}
                  <a
                    className="guide-detail"
                    href={sourceUrl(`skills/${name}/SKILL.md`)}
                  >
                    Full skill instructions on GitHub
                  </a>
                </div>
              </section>
            ))}
          </div>
        </section>
      ))}
    </>
  );
}

const scopeRows = [
  {
    scope: "Project",
    note: "Inside your project repo; committed and shared with your team.",
    files: [
      [".meow/config.toml", "Settings"],
      [".meow/agents/", "Agent roles (.toml)"],
      [".meow/workflows/", "Workflows (.toml)"],
    ],
  },
  {
    scope: "Local",
    note: "Inside your project repo; just for you, ignored by git.",
    files: [
      [".meow/config.local.toml", "Settings"],
      [".meow/local/agents/", "Agent roles (.toml)"],
      [".meow/local/workflows/", "Workflows (.toml)"],
    ],
  },
  {
    scope: "User",
    note: "In your home folder, outside any repo; applies to all projects.",
    files: [
      ["~/.meow/config.toml", "Settings"],
      ["~/.meow/agents/", "Agent roles (.toml)"],
      ["~/.meow/workflows/", "Workflows (.toml)"],
    ],
  },
] as const;

function systemPath(path: string, shell: string) {
  if (shell === "bash") return path;
  const win = path.replaceAll("/", "\\");
  return win.startsWith("~") ? `$env:USERPROFILE${win.slice(1)}` : win;
}

function Scopes() {
  const [shell, setShell] = useState("powershell");
  return (
    <section className="guide-scopes" aria-labelledby="scopes-title">
      <div className="guide-install-heading">
        <h2 id="scopes-title">Where your customizations live</h2>
        <label>
          <span className="sr-only">Your terminal</span>
          <select
            value={shell}
            onChange={(event) => setShell(event.target.value)}
          >
            <option value="powershell">PowerShell</option>
            <option value="bash">Bash</option>
          </select>
        </label>
      </div>
      <p>
        Higher scopes replace lower ones: local, then project, then user, then
        MEOW&apos;s built-ins. Copy a path to open or add a file.
      </p>
      {scopeRows.map(({ scope, note, files }) => (
        <div className="guide-scope-row" key={scope}>
          <h3>
            {scope} <small>{note}</small>
          </h3>
          {files.map(([path, what]) => (
            <div className="guide-path" key={path}>
              <span>{what}</span>
              <Command
                key={shell}
                prefix={
                  path.startsWith(".")
                    ? `your-project${shell === "bash" ? "/" : "\\"}`
                    : undefined
                }
                text={systemPath(path, shell)}
              />
            </div>
          ))}
        </div>
      ))}
      <div className="guide-scope-row">
        <h3>
          Built-in <small>Ship with MEOW; not edited in place.</small>
        </h3>
        <p>
          See what&apos;s included, then eject a copy into a scope to change it.
          Your copy replaces the built-in everywhere that scope applies.
        </p>
        {[
          ["List workflows", "meow workflows list"],
          ["Read a workflow", "meow workflows show quick"],
          ["Change a workflow", "meow workflows eject quick --scope project"],
          ["List agent roles", "meow agents list"],
          ["Change an agent", "meow agents eject reviewer --scope project"],
        ].map(([what, command]) => (
          <div className="guide-path" key={command}>
            <span>{what}</span>
            <Command text={command} />
          </div>
        ))}
        <p className="guide-note">
          Use <code>--scope local</code> or <code>--scope user</code> to keep
          the copy private or personal. The copy lands in that scope&apos;s
          agents or workflows folder above.
        </p>
      </div>
    </section>
  );
}

function MeowUi() {
  return (
    <section className="guide-ui" aria-labelledby="ui-title">
      <h2 id="ui-title">Prefer a visual view?</h2>
      <p>
        <code>meow ui</code> lists every skill, agent, and workflow with its
        scope, and lets you manage workflows and runs.
      </p>
      <Command text="meow ui" />
    </section>
  );
}

function Customize() {
  return (
    <>
      <div className="guide-page-heading">
        <h1>Your project. Your way.</h1>
        <p>
          Tell MEOW what you want to change. It helps you create or edit the
          right definition.
        </p>
      </div>
      <section className="guide-customize" aria-labelledby="customize-title">
        <SlidersHorizontal aria-hidden="true" />
        <div>
          <h2 id="customize-title">Start with a conversation</h2>
          <Command text="/meow:customize" />
          <p>Describe what you need in plain language. Try one of these:</p>
        </div>
      </section>
      <div className="guide-examples">
        <section>
          <h2>A skill</h2>
          <p>Reusable instructions for a task.</p>
          <blockquote>
            “Create a skill for our team’s coding conventions.”
          </blockquote>
        </section>
        <section>
          <h2>An agent</h2>
          <p>A role with its own instructions and tools.</p>
          <blockquote>
            “Create a new agent that audits our API for security problems.”
          </blockquote>
          <blockquote>“Make the reviewer check accessibility.”</blockquote>
        </section>
        <section>
          <h2>A workflow</h2>
          <p>The steps MEOW follows to finish work.</p>
          <blockquote>
            “Create a workflow that reviews database migrations.”
          </blockquote>
          <blockquote>
            “Change the quick workflow to also run our end-to-end tests.”
          </blockquote>
        </section>
      </div>
      <p className="guide-scope">
        Keep changes for yourself, share them with your project, or reuse them
        across projects. Definitions reload on each run.
      </p>
      <nav className="guide-reading" aria-label="Customization details">
        <BookOpen aria-hidden="true" />
        <div>
          <h2>Ready for the details?</h2>
          <p>
            <a href={sourceUrl("skills/customize/SKILL.md")}>
              Customization guide
            </a>
            <a href={sourceUrl("docs/WORKFLOWS.md")}>
              Agent & workflow formats
            </a>
            <a href={sourceUrl("docs/CUSTOM.md")}>Custom sources & scopes</a>
          </p>
        </div>
      </nav>
      <Scopes />
      <MeowUi />
    </>
  );
}

export default function GuidePages({ page }: { page: Page }) {
  return (
    <div className="guide-page">
      {page === "start" ? (
        <Start />
      ) : page === "skills" ? (
        <Skills />
      ) : (
        <Customize />
      )}
      <footer className="guide-footer">
        <span>Looking for more detail?</span>
        <a href="#/README.md">Full reference</a>
        <a href="#/docs/CLI.md">CLI guide</a>
        <a href={sourceUrl("README.md")}>Markdown docs on GitHub</a>
      </footer>
    </div>
  );
}

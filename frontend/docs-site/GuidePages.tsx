import { useState } from "react";
import { BookOpen, Shapes, SlidersHorizontal } from "lucide-react";
import CopyButton from "@/components/CopyButton";
import logo from "../../src/meow/ui/logo.png";
import { runExamples, skillGroups, sourceUrl } from "./content";

export type Page = "start" | "skills" | "customize";

function Command({ text }: { text: string }) {
  const [error, setError] = useState("");
  return (
    <div className="guide-command-wrap">
      <div className="guide-command">
        <code>{text}</code>
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
            A little help.
            <br />A finished branch.
          </h1>
          <p>
            MEOW helps Claude plan, build, and review your code. Start with one
            request.
          </p>
          <a className="guide-primary" href="#setup">
            Set up MEOW
          </a>
          <a className="guide-inline-link" href="#skills">
            Explore the skills
          </a>
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
            <p className="guide-note">
              Run once per project to configure settings, skills, integrations,
              and optional features. Later tasks reuse this setup; run
              onboarding again to add or change something.
            </p>
          </li>
          <li>
            <h3>Ask for your first change</h3>
            <p>Tell MEOW what you want to build.</p>
            <Command text="/meow:run Add CSV export" />
            <p className="guide-note">
              Feature runs use a separate worktree, then commit and push after
              checks pass. Full sprint orchestration also opens a PR/MR by
              default.
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
  return (
    <>
      <div className="guide-page-heading">
        <h1>What would you like to do?</h1>
        <p>Type a skill in Claude Code, then describe what you need.</p>
      </div>
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
                {name === "run" && (
                  <details className="guide-run-examples">
                    <summary>Detailed prompt examples</summary>
                    <p>
                      Describe the change, expected behavior, and how to check
                      it.
                    </p>
                    {runExamples.map(({ title, prompt }) => (
                      <section key={title}>
                        <h4>{title}</h4>
                        <Command text={prompt} />
                      </section>
                    ))}
                  </details>
                )}
                <a
                  className="guide-detail"
                  href={sourceUrl(`skills/${name}/SKILL.md`)}
                >
                  Full skill instructions on GitHub
                </a>
              </section>
            ))}
          </div>
        </section>
      ))}
    </>
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
          <blockquote>“Make the reviewer check accessibility.”</blockquote>
        </section>
        <section>
          <h2>A workflow</h2>
          <p>The steps MEOW follows to finish work.</p>
          <blockquote>
            “Create a workflow that reviews database migrations.”
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

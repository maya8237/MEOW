import { useState } from "react";
import { ArrowRight, Code2, GitBranch, Terminal, Workflow } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  CatalogItem,
  Kind,
  itemKey,
  repoName,
  scopeLabel,
} from "@/lib/catalog";
import CopyButton from "./CopyButton";

const stepKinds: Record<string, string> = {
  uses: "Built-in stage",
  agent: "Agent stage",
  gate: "Review gate",
  loop: "Loop until gates pass",
  parallel: "Parallel stages",
  call: "Nested workflow",
};

export default function ItemDetail({
  item,
  repos,
  kind,
  onCopyError,
  onDuplicate,
  onRun,
}: {
  item: CatalogItem;
  repos: string[];
  kind: Kind;
  onCopyError: (message: string) => void;
  onDuplicate: () => void;
  onRun: () => void;
}) {
  const [sourceOpen, setSourceOpen] = useState(false);
  const workDir = item.repo ? ` --work-dir "${item.repo}"` : "";
  const commands = [
    [
      "Terminal",
      `meow run "<request>" --workflow ${item.name}${workDir} --name "<feature-name>"`,
    ],
    ["Claude Code", `/meow:run --workflow ${item.name} "<request>"`],
  ];
  const facts: [string, string][] = [
    ["Repository", item.repo || "All repositories"],
    [kind === "skills" ? "Directory" : "File", itemKey(item)],
  ];
  if (kind === "agents")
    facts.push(
      ["Role", item.role || item.name],
      ["Model", item.model || "Inherited"],
      [
        "Tools",
        item.tools === null ? "Inherited" : item.tools?.join(", ") || "None",
      ],
      ["Skills", item.skills?.join(", ") || "None"],
    );
  if (kind === "skills")
    facts.push(["Qualified name", item.qualified_name || item.name]);
  return (
    <article className="detail-content">
      <div className="detail-title">
        <div>
          <div className="item-context">
            <Badge variant="secondary">{scopeLabel(item.scope)}</Badge>
            {item.ejected && <Badge variant="outline">Customized</Badge>}
            {item.delegate && <Badge variant="outline">Delegate</Badge>}
            <span>
              {item.repo
                ? repoName(item.repo, repos)
                : "Available across repositories"}
            </span>
          </div>
          <h2>{item.name}</h2>
        </div>
        <Button variant="outline" onClick={onDuplicate}>
          <GitBranch />
          Make a copy
        </Button>
      </div>
      <p className="item-description">
        {item.description || "No description provided."}
      </p>
      {(kind === "workflows" ||
        kind === "skills" ||
        (kind === "agents" && item.claude_resolvable)) && (
        <section className="catalog-run-action" aria-label="Run definition">
          <div>
            <h3>
              {kind === "workflows"
                ? "Run this workflow"
                : kind === "skills"
                  ? "Run this skill"
                  : "Use this agent"}
            </h3>
            <p>
              {kind === "workflows"
                ? "Start a CLI session with this workflow selected."
                : kind === "skills"
                  ? "Start a Claude session with this qualified skill command."
                  : "Start a Claude session with this verified Claude agent."}
            </p>
          </div>
          <Button
            onClick={onRun}
            aria-label={
              kind === "workflows"
                ? `Run workflow ${item.name}`
                : kind === "skills"
                  ? `Run skill ${item.qualified_name || item.name}`
                  : `Run agent ${item.name}`
            }
          >
            {kind === "workflows"
              ? "Run CLI"
              : kind === "skills"
                ? "Run in Claude"
                : "Run with Claude"}
          </Button>
        </section>
      )}
      {kind === "workflows" && (
        <section className="workflow-section" aria-labelledby="steps-title">
          <div className="section-heading">
            <h3 id="steps-title">
              <Workflow />
              Workflow steps
            </h3>
            <span>{item.steps?.length || 0} steps</span>
          </div>
          <ol className="step-flow">
            {(item.steps || []).map((step, index) => (
              <li key={step.id}>
                <div className="step-card">
                  <span className="step-index">{index + 1}</span>
                  <div>
                    <strong>{step.id}</strong>
                    <span>{stepKinds[step.kind] || step.kind}</span>
                  </div>
                </div>
                {index < (item.steps?.length || 0) - 1 && (
                  <ArrowRight className="step-arrow" aria-hidden="true" />
                )}
              </li>
            ))}
          </ol>
          <p className="hint">
            Steps run in order; conditions in the definition decide which ones
            apply. Loop stages repeat until their blocking gates pass or the
            configured round limit is reached. Built-in workflows loop through
            implementation, review, and fixes instead of stopping after one
            attempt.
          </p>
        </section>
      )}
      <section className="properties" aria-labelledby="properties-title">
        <h3 id="properties-title">Definition details</h3>
        <dl>
          {facts.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      </section>
      {kind === "workflows" && (
        <section className="run-section" aria-labelledby="run-title">
          <h3 id="run-title">
            <Terminal />
            Run this workflow
          </h3>
          <p className="hint">
            Replace the request and feature name, then run in your terminal or
            Claude Code.
          </p>
          {commands.map(([label, text]) => (
            <div className="command" key={label}>
              <div className="command-heading">
                <span>{label}</span>
                <CopyButton text={text} onError={onCopyError} />
              </div>
              <pre>{text}</pre>
            </div>
          ))}
        </section>
      )}
      <section className="source-section">
        <div className="section-heading">
          <Button
            variant="ghost"
            onClick={() => setSourceOpen((value) => !value)}
            aria-expanded={sourceOpen}
            aria-controls="definition-source"
          >
            <Code2 />
            {sourceOpen ? "Hide source" : "View source"}
          </Button>
          <CopyButton
            text={item.text}
            label="Copy source"
            onError={onCopyError}
          />
        </div>
        {sourceOpen && <pre id="definition-source">{item.text}</pre>}
      </section>
    </article>
  );
}

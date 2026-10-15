import { useEffect, useState } from "react";
import { request } from "@/lib/catalog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface Option {
  flag: string;
  aliases: string[];
  help: string;
  kind: "switch" | "number" | "value";
  nargs: number | "?" | "+" | "*";
  choices: string[] | null;
  repeatable?: boolean;
  controlled: string | null;
}
interface Entry {
  option?: Option;
  tokens: string[];
  values: string[];
}

function readArguments(args: string[], options: Option[]): Entry[] {
  const known = new Map(
    options.flatMap((option) =>
      option.aliases.map((alias) => [alias, option] as const),
    ),
  );
  const entries: Entry[] = [];
  for (let index = 0; index < args.length;) {
    const start = index;
    const token = args[index++];
    const equal = token.indexOf("=");
    const option = known.get(equal < 0 ? token : token.slice(0, equal));
    const values: string[] = equal < 0 ? [] : [token.slice(equal + 1)];
    if (option && option.kind !== "switch") {
      const multiple = option.nargs === "+" || option.nargs === "*";
      while (index < args.length && (multiple || !values.length)) {
        if (args[index].startsWith("--") || known.has(args[index])) break;
        values.push(args[index++]);
      }
    }
    entries.push({ option, tokens: args.slice(start, index), values });
  }
  return entries;
}

export default function AdvancedOptions({
  command,
  arguments: args,
  onChange,
}: {
  command: string;
  arguments: string[];
  onChange: (args: string[]) => void;
}) {
  const [options, setOptions] = useState<Option[] | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void request<{ commands: Record<string, Option[]> }>("/api/cli-options", {
      signal: controller.signal,
    })
      .then((result) => {
        if (!controller.signal.aborted) {
          setOptions(result.commands[command] || []);
          setError("");
        }
      })
      .catch((reason) => {
        if (!controller.signal.aborted)
          setError(
            reason instanceof Error
              ? reason.message
              : "Could not load CLI options.",
          );
      });
    return () => controller.abort();
  }, [command, revision]);
  if (error)
    return (
      <div role="alert">
        {error}
        <Button
          type="button"
          variant="outline"
          onClick={() => setRevision((value) => value + 1)}
        >
          Retry options
        </Button>
      </div>
    );
  if (!options) return <p role="status">Loading MEOW flags…</p>;
  const entries = readArguments(args, options);
  function replace(entry: Entry, values?: string[]) {
    onChange(
      entries.flatMap((item) => (item === entry ? values || [] : item.tokens)),
    );
  }
  function setValues(entry: Entry, values: string[]) {
    replace(entry, [
      entry.option!.flag,
      ...(entry.option!.nargs === "?" ? values.filter(Boolean) : values),
    ]);
  }
  return (
    <div className="advanced-options">
      {options.map((option) => {
        const selected = entries.filter((entry) => entry.option === option);
        return (
          <fieldset className="advanced-option" key={option.flag}>
            <label className="advanced-option-label">
              <input
                type="checkbox"
                aria-label={option.flag}
                checked={!!selected.length}
                disabled={!!option.controlled}
                onChange={(event) =>
                  onChange(
                    event.target.checked
                      ? [...args, option.flag]
                      : entries
                          .filter((entry) => entry.option !== option)
                          .flatMap((entry) => entry.tokens),
                  )
                }
              />
              <code>{option.aliases.join(", ")}</code>
            </label>
            <p>{option.help}</p>
            {option.controlled && <small>{option.controlled}</small>}
            {selected.map((entry, entryIndex) => (
              <div key={entryIndex}>
                {!option.controlled &&
                  option.kind !== "switch" &&
                  (entry.values.length ? entry.values : [""]).map(
                    (value, valueIndex) => {
                      const label = `${option.flag} value${option.nargs === "+" || option.nargs === "*" ? ` ${valueIndex + 1}` : ""}`;
                      const change = (value: string) => {
                        const values = entry.values.length
                          ? [...entry.values]
                          : [""];
                        values[valueIndex] = value;
                        setValues(entry, values);
                      };
                      return (
                        <div className="advanced-value" key={valueIndex}>
                          <label className="session-field">
                            {label}
                            {option.choices ? (
                              <select
                                aria-label={label}
                                value={value}
                                onChange={(event) => change(event.target.value)}
                              >
                                <option value="">
                                  {option.nargs === "?"
                                    ? "Use without a value"
                                    : "Choose a value"}
                                </option>
                                {option.choices.map((choice) => (
                                  <option key={choice} value={choice}>
                                    {choice}
                                  </option>
                                ))}
                              </select>
                            ) : (
                              <Input
                                aria-label={label}
                                type={
                                  option.kind === "number" ? "number" : "text"
                                }
                                value={value}
                                onChange={(event) => change(event.target.value)}
                                placeholder={
                                  option.nargs === "?"
                                    ? "Optional value"
                                    : "Required value"
                                }
                              />
                            )}
                          </label>
                          {(option.nargs === "+" || option.nargs === "*") && (
                            <Button
                              type="button"
                              variant="outline"
                              aria-label={`Remove ${label}`}
                              onClick={() => {
                                if (entry.values.length <= 1) replace(entry);
                                else
                                  setValues(
                                    entry,
                                    entry.values.filter(
                                      (_, index) => index !== valueIndex,
                                    ),
                                  );
                              }}
                            >
                              Remove value
                            </Button>
                          )}
                        </div>
                      );
                    },
                  )}
                {!option.controlled &&
                  (option.nargs === "+" || option.nargs === "*") && (
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => setValues(entry, [...entry.values, ""])}
                    >
                      Add value for {option.flag}
                    </Button>
                  )}
                <Button
                  type="button"
                  variant="outline"
                  aria-label={`Remove ${option.flag}`}
                  onClick={() => replace(entry)}
                >
                  Remove
                </Button>
              </div>
            ))}
            {option.repeatable && !option.controlled && !!selected.length && (
              <Button
                type="button"
                variant="outline"
                onClick={() => onChange([...args, option.flag])}
              >
                Add another {option.flag}
              </Button>
            )}
          </fieldset>
        );
      })}
      {entries
        .filter((entry) => !entry.option)
        .map((entry, index) => (
          <div className="recipe-row" key={index}>
            <code>Saved argument: {entry.tokens.join(" ")}</code>
            <Button
              type="button"
              variant="outline"
              aria-label={`Remove saved argument ${index + 1}`}
              onClick={() => replace(entry)}
            >
              Remove
            </Button>
          </div>
        ))}
    </div>
  );
}

import { ExternalLink } from "lucide-react";
import { DOCS_SITE_URL } from "@/lib/links";

// Plain link only: the local app never fetches the hosted site.
export default function OnlineDocsLink() {
  return (
    <a
      className="text-sm text-muted-foreground underline-offset-4 hover:underline inline-flex items-center gap-1"
      href={DOCS_SITE_URL}
      target="_blank"
      rel="noreferrer"
    >
      Online docs
      <ExternalLink className="size-3.5" aria-hidden="true" />
    </a>
  );
}

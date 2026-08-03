import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Code2, Check } from "lucide-react";
import { LANGUAGE_REGISTRY } from "@/lib/editorTemplates";

interface LanguageSelectorProps {
  value: string;
  onChange: (value: string) => void;
  /**
   * Languages the current problem actually ships a starter template for.
   * Omit to keep the previous behaviour (every option shown unannotated).
   * Options outside this list stay selectable — a user may still want to
   * write from scratch — but are labelled so the choice is informed rather
   * than a surprise blank editor.
   */
  available?: string[];
}

// Derived from the backend registry rather than hand-maintained (M4 Phase B
// polish). This list previously duplicated labels and extensions that
// common/languages.py already carried, making it the third of four copies of
// the same data. See src/lib/editorTemplates.ts.
export const LANGUAGES: { value: string; label: string; ext: string }[] =
  LANGUAGE_REGISTRY.map((l) => ({
    value: l.key,
    label: l.label,
    ext: `.${l.extension}`,
  }));

/**
 * File extension for a language, without the leading dot.
 *
 * Exported so callers reuse this table instead of re-deriving extensions.
 * The editor's filename label previously hardcoded a ternary chain that fell
 * through to "cpp", so selecting C or JavaScript displayed "solution.cpp".
 */
export function extensionFor(value: string): string {
  const match = LANGUAGES.find((l) => l.value === value);
  return (match?.ext ?? ".txt").replace(/^\./, "");
}

export default function LanguageSelector({
  value,
  onChange,
  available,
}: LanguageSelectorProps) {
  const active = LANGUAGES.find((l) => l.value === value) ?? LANGUAGES[0];
  // Undefined = caller did not supply availability, so annotate nothing.
  const hasTemplate = (lang: string) => !available || available.includes(lang);

  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger
        data-testid="language-selector-trigger"
        className="group h-9 w-[180px] px-3 gap-2 rounded-md
          bg-card/40 backdrop-blur-md border border-border/60
          text-foreground text-sm font-medium
          hover:border-primary/40 hover:bg-card/60
          hover:shadow-[0_0_15px_rgba(99,102,241,0.2)]
          focus:outline-none focus:ring-1 focus:ring-primary/50 focus:border-primary/50
          data-[state=open]:border-primary/50
          data-[state=open]:shadow-[0_0_18px_rgba(99,102,241,0.25)]
          transition-all duration-200"
      >
        <div className="flex items-center gap-2 min-w-0">
          <div className="h-5 w-5 rounded-md border border-border/60 bg-background/40 flex items-center justify-center shrink-0 group-hover:border-primary/40 group-hover:text-primary transition-colors">
            <Code2 className="h-3 w-3 text-primary" />
          </div>
          <SelectValue placeholder="Select language">
            <span className="flex items-center gap-1.5">
              <span className="truncate">{active.label}</span>
              <span className="text-[10px] font-mono text-muted-foreground tracking-wider">
                {active.ext}
              </span>
            </span>
          </SelectValue>
        </div>
      </SelectTrigger>

      <SelectContent
        data-testid="language-selector-content"
        className="bg-card/95 backdrop-blur-xl border-border/60 shadow-[0_8px_30px_rgba(0,0,0,0.5)] min-w-[200px]"
      >
        <div className="px-2 pt-1.5 pb-2 mb-1 border-b border-border/60">
          <p className="text-[10px] font-mono uppercase tracking-[0.22em] text-muted-foreground">
            Language
          </p>
        </div>

        {LANGUAGES.map((lang) => {
          const isSelected = lang.value === value;
          return (
            <SelectItem
              key={lang.value}
              value={lang.value}
              data-testid={`language-option-${lang.value}`}
              className="group/item relative pl-8 pr-3 py-2 rounded-md cursor-pointer
                text-sm text-muted-foreground
                focus:bg-primary/10 focus:text-primary
                data-[state=checked]:text-primary
                data-[highlighted]:bg-primary/10
                data-[highlighted]:text-primary
                transition-colors"
            >
              <span className="absolute left-2 top-1/2 -translate-y-1/2 flex items-center justify-center w-4 h-4">
                {isSelected ? (
                  <Check className="h-3.5 w-3.5 text-primary drop-shadow-[0_0_6px_rgba(99,102,241,0.7)]" />
                ) : (
                  <Code2 className="h-3 w-3 text-muted-foreground/60 group-hover/item:text-primary/70" />
                )}
              </span>

              <div className="flex items-center justify-between gap-3">
                <span className="font-medium">{lang.label}</span>
                {hasTemplate(lang.value) ? (
                  <span className="text-[10px] font-mono text-muted-foreground/80 tracking-wider">
                    {lang.ext}
                  </span>
                ) : (
                  <span
                    data-testid={`language-no-template-${lang.value}`}
                    className="text-[10px] font-mono text-amber-300/80 tracking-wider"
                  >
                    no template
                  </span>
                )}
              </div>
            </SelectItem>
          );
        })}
      </SelectContent>
    </Select>
  );
}

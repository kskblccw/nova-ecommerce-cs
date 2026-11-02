import { Sparkles } from "lucide-react";

export function BrandMark({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-3">
      <div
        className="relative flex h-10 w-10 items-center justify-center rounded-2xl text-white shadow-lg"
        style={{
          background: "linear-gradient(135deg, var(--brand-strong), var(--brand))",
          boxShadow: "0 10px 24px -12px var(--brand)",
        }}
      >
        <Sparkles className="h-5 w-5" />
      </div>
      {!compact && (
        <div className="leading-tight">
          <div className="text-[15px] font-semibold tracking-tight">Nova 客服</div>
          <div className="text-[11px] tracking-wide" style={{ color: "var(--faint)" }}>
            E-COMMERCE AGENT
          </div>
        </div>
      )}
    </div>
  );
}

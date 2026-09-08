/** 공용 UI 조각. 시안의 카드·배지·표 스타일을 한 곳에 모아 화면들이 같은 톤을 유지하게 한다. */
import type { ReactNode } from "react";

import { IconClose } from "./icons";

export const cx = (...parts: (string | false | null | undefined)[]) =>
  parts.filter(Boolean).join(" ");

/* ────────────────────────────────────────────────── 레이아웃 */

export function Section({
  title,
  desc,
  actions,
  children,
  className,
}: {
  title: string;
  desc?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cx("mb-7", className)}>
      <div className="mb-3 flex items-end justify-between gap-4">
        <div>
          <h2 className="text-[15.5px] font-semibold tracking-tight text-ink">{title}</h2>
          {desc && <p className="mt-[3px] text-[12.5px] text-muted">{desc}</p>}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>
      {children}
    </section>
  );
}

export function Card({
  children,
  className,
  padded = true,
}: {
  children: ReactNode;
  className?: string;
  padded?: boolean;
}) {
  return (
    <div
      className={cx(
        "rounded-lg border border-hairline bg-canvas",
        padded && "p-6",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function CardTitle({ title, desc }: { title: string; desc?: string }) {
  return (
    <div className="mb-4">
      <div className="text-[14.5px] font-semibold tracking-tight text-ink">{title}</div>
      {desc && <div className="mt-1 text-[12px] text-muted">{desc}</div>}
    </div>
  );
}

/* ────────────────────────────────────────────────── KPI */

export function KpiCard({
  label,
  value,
  sub,
  variant = "default",
  onClick,
}: {
  label: string;
  value: number | string;
  sub?: string;
  variant?: "default" | "warn" | "accent";
  onClick?: () => void;
}) {
  const tone =
    variant === "accent"
      ? "text-primary-active"
      : variant === "warn"
        ? "text-warning"
        : "text-ink";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={!onClick}
      className={cx(
        "rounded-lg border border-hairline bg-canvas px-5 py-[18px] text-left transition-colors",
        onClick && "cursor-pointer hover:border-primary/50 hover:bg-surface-soft",
      )}
    >
      <div className="text-[12px] font-medium text-muted">{label}</div>
      <div className={cx("tnum mt-2 text-[30px] font-semibold leading-none", tone)}>{value}</div>
      {sub && <div className="mt-[7px] text-[11.5px] text-muted-soft">{sub}</div>}
    </button>
  );
}

/* ────────────────────────────────────────────────── 상태 표시 */

export function Dot({ ok, className }: { ok: boolean; className?: string }) {
  return (
    <span
      className={cx(
        "inline-block h-[7px] w-[7px] rounded-full",
        ok ? "bg-success" : "bg-error",
        className,
      )}
    />
  );
}

/* ────────────────────────────────────────────────── 입력 */

export function Button({
  children,
  variant = "ghost",
  size = "md",
  className,
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost" | "danger";
  size?: "sm" | "md";
}) {
  const base =
    "inline-flex items-center justify-center gap-[6px] rounded-md font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50";
  const sizes = size === "sm" ? "h-[30px] px-3 text-[12.5px]" : "h-[36px] px-4 text-[13.5px]";
  const tones = {
    primary: "bg-primary text-canvas hover:bg-primary-active",
    ghost: "border border-hairline bg-canvas text-body hover:bg-surface-soft hover:text-ink",
    danger: "border border-error/40 bg-canvas text-error hover:bg-error/10",
  }[variant];
  return (
    <button className={cx(base, sizes, tones, className)} {...rest}>
      {children}
    </button>
  );
}

export function Field({
  label,
  hint,
  children,
  className,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={cx("block", className)}>
      <span className="mb-[5px] block text-[11.5px] font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="mt-[5px] block text-[11px] text-muted-soft">{hint}</span>}
    </label>
  );
}

const inputBase =
  "h-[36px] w-full rounded-md border border-hairline bg-canvas px-3 text-[13.5px] text-body outline-none transition-colors placeholder:text-muted-soft focus:border-primary";

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cx(inputBase, props.className)} />;
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={cx(inputBase, "pr-8", props.className)} />;
}

export function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className={cx(
        inputBase,
        "h-auto min-h-[80px] resize-y py-2 leading-relaxed",
        props.className,
      )}
    />
  );
}

export function Checkbox({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-2 text-[13px] text-body">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-[15px] w-[15px] accent-[#cc785c]"
      />
      {label}
    </label>
  );
}

/* ────────────────────────────────────────────────── 탭 */

export function Tabs<T extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: { id: T; label: string }[];
  active: T;
  onChange: (id: T) => void;
}) {
  return (
    <div className="mb-4 inline-flex rounded-lg border border-hairline bg-surface-soft p-[3px]">
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          onClick={() => onChange(t.id)}
          className={cx(
            "rounded-[6px] px-[14px] py-[6px] text-[12.5px] font-medium transition-colors",
            active === t.id
              ? "bg-canvas text-ink shadow-[0_1px_2px_rgba(20,20,19,.06)]"
              : "text-muted hover:text-body-strong",
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

/* ────────────────────────────────────────────────── 표 */

export function Table({ head, children }: { head: string[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[13.5px]">
        <thead>
          <tr>
            {head.map((h) => (
              <th
                key={h}
                className="whitespace-nowrap border-b border-hairline px-[14px] pb-3 text-left text-[12px] font-semibold tracking-[0.02em] text-muted"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export function Td({
  children,
  className,
  ...rest
}: React.TdHTMLAttributes<HTMLTableCellElement>) {
  return (
    <td
      {...rest}
      className={cx("border-b border-hairline/60 px-[14px] py-[13px] text-body", className)}
    >
      {children}
    </td>
  );
}

export function EmptyRow({ colSpan, text }: { colSpan: number; text: string }) {
  return (
    <tr>
      <td colSpan={colSpan} className="px-4 py-12 text-center text-[13px] text-muted-soft">
        {text}
      </td>
    </tr>
  );
}

/* ────────────────────────────────────────────────── 모달 */

export function Modal({
  open,
  onClose,
  title,
  width = 620,
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  width?: number;
  children: ReactNode;
  footer?: ReactNode;
}) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[rgba(20,20,19,.45)] p-4"
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        style={{ width: `min(${width}px, 94vw)` }}
        className="max-h-[92vh] overflow-hidden rounded-xl border border-hairline bg-canvas shadow-[0_18px_50px_rgba(20,20,19,.18)]"
      >
        <div className="flex items-center justify-between border-b border-hairline px-[22px] py-[15px]">
          <h3 className="text-[15px] font-semibold text-ink">{title}</h3>
          <button
            type="button"
            onClick={onClose}
            aria-label="닫기"
            className="text-muted-soft transition-colors hover:text-ink"
          >
            <IconClose />
          </button>
        </div>
        <div className="max-h-[70vh] overflow-y-auto px-[22px] py-[18px]">{children}</div>
        {footer && (
          <div className="flex items-center justify-end gap-2 border-t border-hairline px-[22px] py-[14px]">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}

export function ErrorText({ children }: { children: ReactNode }) {
  if (!children) return null;
  return <p className="text-[12.5px] text-error">{children}</p>;
}

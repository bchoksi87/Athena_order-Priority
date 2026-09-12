/** Form field primitives: label + control + helper text; every value change is typed. */
import type { ReactNode } from "react";

import "./Field.css";

interface BaseFieldProps {
  label: ReactNode;
  help?: ReactNode;
  error?: string | null;
  disabled?: boolean;
  required?: boolean;
  /** Inline layout (label left, control right) for dense forms. */
  inline?: boolean;
  id?: string;
}

function Wrapper({ label, help, error, required, inline, children }: BaseFieldProps & { children: ReactNode }) {
  return (
    <label className={`field fld${inline ? " fld-inline" : ""}${error ? " fld-error" : ""}`}>
      <span className="label">
        {label}
        {required ? <span className="fld-req"> *</span> : null}
      </span>
      {children}
      {error ? <span className="fld-msg fld-msg-error">{error}</span> : help ? <span className="fld-msg">{help}</span> : null}
    </label>
  );
}

export interface NumberFieldProps extends BaseFieldProps {
  value: number | null;
  onChange: (value: number | null) => void;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  /** Allow the field to be cleared (null). */
  nullable?: boolean;
  placeholder?: string;
}

export function NumberField({ value, onChange, min, max, step, unit, nullable = false, placeholder, ...rest }: NumberFieldProps) {
  return (
    <Wrapper {...rest}>
      <span className="fld-control">
        <input
          id={rest.id}
          className="input num"
          type="number"
          value={value ?? ""}
          min={min}
          max={max}
          step={step ?? "any"}
          disabled={rest.disabled}
          placeholder={placeholder ?? (nullable ? "blank" : undefined)}
          onChange={(e) => {
            if (e.target.value === "") {
              onChange(nullable ? null : 0);
              return;
            }
            const n = Number(e.target.value);
            if (!Number.isNaN(n)) onChange(n);
          }}
        />
        {unit ? <span className="fld-unit">{unit}</span> : null}
      </span>
    </Wrapper>
  );
}

export interface TextFieldProps extends BaseFieldProps {
  value: string;
  onChange: (value: string) => void;
  type?: "text" | "password" | "datetime-local" | "date" | "email";
  placeholder?: string;
  autoComplete?: string;
  mono?: boolean;
  list?: string;
}

export function TextField({ value, onChange, type = "text", placeholder, autoComplete, mono, list, ...rest }: TextFieldProps) {
  return (
    <Wrapper {...rest}>
      <input id={rest.id} className={`input${mono ? " mono" : ""}`} type={type} value={value} placeholder={placeholder} autoComplete={autoComplete} disabled={rest.disabled} list={list} onChange={(e) => onChange(e.target.value)} />
    </Wrapper>
  );
}

export interface TextAreaFieldProps extends BaseFieldProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  rows?: number;
}

export function TextAreaField({ value, onChange, placeholder, rows = 3, ...rest }: TextAreaFieldProps) {
  return (
    <Wrapper {...rest}>
      <textarea id={rest.id} className="textarea" value={value} placeholder={placeholder} rows={rows} disabled={rest.disabled} onChange={(e) => onChange(e.target.value)} />
    </Wrapper>
  );
}

export interface SelectFieldProps<V extends string> extends BaseFieldProps {
  value: V;
  onChange: (value: V) => void;
  options: ReadonlyArray<{ value: V; label: string }>;
  placeholder?: string;
}

export function SelectField<V extends string>({ value, onChange, options, placeholder, ...rest }: SelectFieldProps<V>) {
  return (
    <Wrapper {...rest}>
      <select id={rest.id} className="select" value={value} disabled={rest.disabled} onChange={(e) => onChange(e.target.value as V)}>
        {placeholder !== undefined ? <option value="">{placeholder}</option> : null}
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </Wrapper>
  );
}

export interface CheckFieldProps {
  label: ReactNode;
  checked: boolean;
  onChange: (checked: boolean) => void;
  help?: ReactNode;
  disabled?: boolean;
}

export function CheckField({ label, checked, onChange, help, disabled }: CheckFieldProps) {
  return (
    <label className="fld-check">
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span>
        <span>{label}</span>
        {help ? <span className="fld-msg">{help}</span> : null}
      </span>
    </label>
  );
}

/** Group of toggle chips for a string list (batching dimensions, trigger events, roles). */
export function ChipListField({ label, help, value, options, onChange, disabled }: { label: ReactNode; help?: ReactNode; value: string[]; options: readonly string[]; onChange: (v: string[]) => void; disabled?: boolean }) {
  const all = Array.from(new Set([...options, ...value]));
  return (
    <div className="field fld">
      <span className="label">{label}</span>
      <div className="row row-wrap gap-1">
        {all.map((opt) => {
          const on = value.includes(opt);
          return (
            <button key={opt} type="button" className={`chip${on ? " active" : ""}`} disabled={disabled} aria-pressed={on} onClick={() => onChange(on ? value.filter((v) => v !== opt) : [...value, opt])}>
              {opt}
            </button>
          );
        })}
      </div>
      {help ? <span className="fld-msg">{help}</span> : null}
    </div>
  );
}

/** Editable key → number table (tier scores, ERP priority points, quality weights). */
export function NumberMapField({ label, help, value, onChange, disabled, keyLabel = "Key", valueLabel = "Value", fixedKeys }: { label: ReactNode; help?: ReactNode; value: Record<string, number>; onChange: (v: Record<string, number>) => void; disabled?: boolean; keyLabel?: string; valueLabel?: string; fixedKeys?: readonly string[] }) {
  const keys = fixedKeys ? Array.from(new Set([...fixedKeys, ...Object.keys(value)])) : Object.keys(value);
  const rename = (from: string, to: string) => {
    const next: Record<string, number> = {};
    for (const k of Object.keys(value)) next[k === from ? to : k] = value[k] ?? 0;
    onChange(next);
  };
  return (
    <div className="field fld">
      <span className="label">{label}</span>
      <table className="fld-map">
        <thead>
          <tr>
            <th>{keyLabel}</th>
            <th>{valueLabel}</th>
            {!fixedKeys ? <th /> : null}
          </tr>
        </thead>
        <tbody>
          {keys.map((k) => (
            <tr key={k}>
              <td>
                {fixedKeys ? (
                  <span className="mono">{k}</span>
                ) : (
                  <input className="input mono" value={k} disabled={disabled} aria-label={`${keyLabel} ${k}`} onChange={(e) => rename(k, e.target.value)} />
                )}
              </td>
              <td>
                <input className="input num" type="number" step="any" value={value[k] ?? ""} disabled={disabled} aria-label={`${valueLabel} ${k}`} onChange={(e) => onChange({ ...value, [k]: Number(e.target.value) })} />
              </td>
              {!fixedKeys ? (
                <td>
                  <button
                    type="button"
                    className="btn btn-sm btn-ghost"
                    disabled={disabled}
                    aria-label={`Remove ${k}`}
                    onClick={() => {
                      const next = { ...value };
                      delete next[k];
                      onChange(next);
                    }}
                  >
                    ×
                  </button>
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
      {!fixedKeys ? (
        <button
          type="button"
          className="btn btn-sm"
          disabled={disabled}
          onClick={() => {
            let i = 1;
            while (`key_${i}` in value) i += 1;
            onChange({ ...value, [`key_${i}`]: 0 });
          }}
        >
          + Add
        </button>
      ) : null}
      {help ? <span className="fld-msg">{help}</span> : null}
    </div>
  );
}

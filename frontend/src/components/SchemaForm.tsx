/** Generic editor for one configuration section, driven by the field descriptors in lib/configSchema. */
import type { FieldSchema, SectionSchema } from "@/lib/configSchema";

import { CheckField, ChipListField, NumberField, NumberMapField, SelectField } from "./Field";

export interface SchemaFormProps {
  schema: SectionSchema;
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  disabled?: boolean;
  /** Two-column layout for numeric fields. */
  columns?: 1 | 2;
}

function FieldControl({ field, value, onChange, disabled }: { field: FieldSchema; value: unknown; onChange: (v: unknown) => void; disabled?: boolean }) {
  switch (field.type) {
    case "number":
    case "integer":
      return (
        <NumberField
          label={field.label}
          help={field.help}
          value={typeof value === "number" ? value : null}
          min={field.min}
          max={field.max}
          step={field.step ?? (field.type === "integer" ? 1 : undefined)}
          unit={field.unit}
          nullable={field.nullable}
          disabled={disabled}
          onChange={(v) => onChange(field.type === "integer" && v !== null ? Math.round(v) : v)}
          inline
        />
      );
    case "boolean":
      return <CheckField label={field.label} help={field.help} checked={Boolean(value)} disabled={disabled} onChange={onChange} />;
    case "enum":
      return (
        <SelectField label={field.label} help={field.help} value={typeof value === "string" ? value : ""} options={field.options.map((o) => ({ value: o, label: o }))} disabled={disabled} onChange={onChange} inline />
      );
    case "map-number":
      return (
        <NumberMapField
          label={field.label}
          help={field.help}
          value={(value as Record<string, number> | undefined) ?? {}}
          keyLabel={field.keyLabel}
          valueLabel={field.valueLabel}
          fixedKeys={field.fixedKeys}
          disabled={disabled}
          onChange={onChange}
        />
      );
    case "string-list":
      return <ChipListField label={field.label} help={field.help} value={Array.isArray(value) ? (value as string[]) : []} options={field.options ?? []} disabled={disabled} onChange={onChange} />;
  }
}

export function SchemaForm({ schema, value, onChange, disabled, columns = 1 }: SchemaFormProps) {
  return (
    <div className={`col gap-1${columns === 2 ? " grid grid-2" : ""}`} data-section={schema.key}>
      {schema.fields.map((field) => (
        <FieldControl key={field.key} field={field} value={value[field.key]} disabled={disabled} onChange={(v) => onChange({ ...value, [field.key]: v })} />
      ))}
    </div>
  );
}

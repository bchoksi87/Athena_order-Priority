import "./Layout.css";

export interface TabItem<K extends string = string> {
  key: K;
  label: string;
  count?: number;
}

export interface TabsProps<K extends string = string> {
  items: TabItem<K>[];
  value: K;
  onChange: (key: K) => void;
  ariaLabel?: string;
}

/** Controlled underline tabs. */
export function Tabs<K extends string = string>({ items, value, onChange, ariaLabel }: TabsProps<K>) {
  return (
    <div className="tabs" role="tablist" aria-label={ariaLabel}>
      {items.map((item) => (
        <button
          key={item.key}
          type="button"
          role="tab"
          aria-selected={item.key === value}
          className={`tab${item.key === value ? " active" : ""}`}
          onClick={() => onChange(item.key)}
        >
          {item.label}
          {item.count !== undefined ? <span className="tab-count">{item.count}</span> : null}
        </button>
      ))}
    </div>
  );
}

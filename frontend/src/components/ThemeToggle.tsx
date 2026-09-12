import { useTheme } from "@/app/theme";

/** Dark/light toggle; a third click returns to the OS preference. */
export function ThemeToggle() {
  const { preference, resolved, setPreference } = useTheme();
  const next = preference === "system" ? (resolved === "dark" ? "light" : "dark") : preference === "dark" ? "light" : "system";
  const label = preference === "system" ? `Auto (${resolved})` : preference === "dark" ? "Dark" : "Light";
  return (
    <button type="button" className="btn btn-sm btn-ghost" onClick={() => setPreference(next)} title={`Theme: ${label}. Click to switch.`}>
      <span aria-hidden="true">{resolved === "dark" ? "◐" : "◑"}</span>
      <span className="text-xs">{label}</span>
    </button>
  );
}

import type { PluginConfig, PluginInfo } from "../api/types";
import { JsonSetting, SchemaFields } from "./PluginSchemaField";
import i18n from "../i18n";

export function PluginSettingsForm({
  plugin,
  metadata,
  onChange,
}: {
  plugin: PluginConfig;
  metadata?: PluginInfo;
  onChange: (settings: Record<string, unknown>) => void;
}) {
  const properties = metadata?.settings_schema?.properties ?? {};
  const record = { ...metadata?.defaults, ...plugin.settings };
  return (
    <>
      <SchemaFields
        properties={properties}
        value={record}
        onChange={onChange}
        extraKeys={Object.keys(record)}
      />
      <details>
        <summary>{i18n.t("plugins:schema.editJson")}</summary>
        <JsonSetting
          label={i18n.t("plugins:schema.jsonLabel")}
          value={record}
          onChange={(value) => onChange(value as Record<string, unknown>)}
        />
      </details>
    </>
  );
}

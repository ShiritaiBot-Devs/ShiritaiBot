import type { ModelConfig } from "../api/types";
import { configuredModelName, groupModels } from "../lib/models";
import i18n from "../i18n";

export function ModelOptions({ models }: { models: ModelConfig[] }) {
  return [...groupModels(models)].map(([provider, group]) => (
    <optgroup key={provider} label={provider || i18n.t("unknownProvider")}>
      {group.map((model) => (
        <option key={model.id} value={model.id}>
          {group.some((item) => item !== model && item.model === model.model)
            ? i18n.t("withDetail", {
                label: configuredModelName(model),
                detail: model.id,
              })
            : configuredModelName(model)}
        </option>
      ))}
    </optgroup>
  ));
}

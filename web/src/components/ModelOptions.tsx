import type { ModelConfig } from "../api/types";
import { configuredModelName, groupModels } from "../lib/models";
import i18n from "../i18n";

export function ModelOptions({ models }: { models: ModelConfig[] }) {
  return [...groupModels(models)].map(([provider, group]) => (
    <optgroup key={provider} label={provider || i18n.t("unknownProvider")}>
      {group.map((model) => (
        <option key={model.id} value={model.id}>
          {configuredModelName(model)}
          {group.some((item) => item !== model && item.model === model.model)
            ? `（${model.id}）`
            : ""}
        </option>
      ))}
    </optgroup>
  ));
}

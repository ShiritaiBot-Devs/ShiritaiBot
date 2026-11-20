import type { ModelConfig } from "../api/types";
import { configuredModelName, groupModels } from "../lib/models";

export function ModelOptions({ models }: { models: ModelConfig[] }) {
  return [...groupModels(models)].map(([provider, group]) => (
    <optgroup key={provider} label={provider || "未知提供商"}>
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

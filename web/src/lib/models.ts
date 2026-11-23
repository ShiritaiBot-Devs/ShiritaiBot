import type { ModelConfig } from "../api/types";
import i18n from "../i18n";

export function modelName(provider: string, model: string) {
  return `${provider || i18n.t("unknownProvider")}/${model || i18n.t("unknownModel")}`;
}

export function configuredModelName(model: ModelConfig) {
  return modelName(model.provider, model.model || model.id);
}

export function modelNameById(models: ModelConfig[], id: string) {
  const model = models.find((item) => item.id === id);
  return model ? configuredModelName(model) : modelName("", id);
}

export function groupModels(models: ModelConfig[]) {
  const groups = new Map<string, ModelConfig[]>();
  for (const model of models) {
    const group = groups.get(model.provider);
    if (group) group.push(model);
    else groups.set(model.provider, [model]);
  }
  return groups;
}

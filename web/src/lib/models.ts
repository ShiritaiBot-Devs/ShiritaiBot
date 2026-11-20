import type { ModelConfig } from "../api/types";

export function modelName(provider: string, model: string) {
  return `${provider || "未知提供商"}/${model || "未知模型"}`;
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

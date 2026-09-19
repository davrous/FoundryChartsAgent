import assert from "node:assert/strict";
import fs from "node:fs";

const read = (path) => JSON.parse(fs.readFileSync(new URL(path, import.meta.url), "utf8"));
const root = read("./.state/main.json");
const parameters = read("./main.parameters.json").parameters;
const resources = (template) => Object.values(template.resources ?? {});
const deployment = (template, name) => {
  const result = resources(template).find(
    (resource) => resource.properties?.template &&
      (resource.name === name || resource.name === `[parameters('${name}')]`),
  );
  assert.ok(result, `Missing deployment: ${name}`);
  return result;
};
const unwrap = (resource) => resource.properties.template;
const values = (resource) => Object.fromEntries(
  Object.entries(resource.properties.parameters).map(([key, parameter]) => [key, parameter.value]),
);
const component = (name, innerName) =>
  deployment(unwrap(deployment(root, name)), innerName);

for (const key of Object.keys(parameters)) {
  assert.ok(key in root.parameters, `Undeclared parameter: ${key}`);
}
assert.deepEqual(
  Object.entries(root.parameters)
    .filter(([key, value]) => !("defaultValue" in value) && !(key in parameters))
    .map(([key]) => key),
  ["gatewayApiClientId"],
);
assert.equal(root.parameters.gatewayApiClientId.minLength, 36);
assert.equal(root.parameters.gatewayApiClientId.maxLength, 36);
assert.equal(root.parameters.foundryAgentVersion.defaultValue, "2");
assert.ok(!resources(root).some((resource) => resource.type === "Microsoft.Resources/resourceGroups"));

const plan = values(component("appServicePlan", "appServicePlanName"));
assert.equal(plan.skuName, "B1");
assert.equal(plan.skuCapacity, 1);
assert.equal(plan.reserved, true);
assert.equal(plan.zoneRedundant, false);

const appWrapper = unwrap(deployment(root, "appService"));
const app = values(deployment(appWrapper, "appServiceName"));
assert.equal(app.siteConfig.linuxFxVersion, "PYTHON|3.13");
assert.equal(app.siteConfig.appCommandLine, "python -m gateway.main");
assert.equal(app.siteConfig.alwaysOn, true);
assert.equal(app.siteConfig.healthCheckPath, "/health");
assert.equal(app.siteConfig.minTlsVersion, "1.2");
assert.equal(app.siteConfig.scmMinTlsVersion, "1.2");
assert.equal(app.siteConfig.ftpsState, "Disabled");
assert.equal(app.httpsOnly, true);
assert.equal(app.managedIdentities.systemAssigned, true);
assert.deepEqual(app.basicPublishingCredentialsPolicies, [
  { name: "scm", allow: false },
  { name: "ftp", allow: false },
]);

const settingsDeployment = resources(appWrapper).find(
  (resource) => resource.properties?.parameters?.name?.value === "appsettings",
);
assert.ok(settingsDeployment, "Missing post-creation app settings deployment");
const settings = values(settingsDeployment);
const env = settings.properties;
assert.equal(settings.applicationInsightResourceId, "[parameters('appInsightsResourceId')]");
assert.equal(env.GATEWAY_MODE, "foundry");
assert.equal(env.GATEWAY_HOST, "0.0.0.0");
assert.equal(env.GATEWAY_PORT, "8000");
assert.equal(env.CHARTS_DEV_MODE, "false");
assert.equal(env.ApplicationInsightsAgent_EXTENSION_VERSION, "~3");
assert.equal(env.SCM_DO_BUILD_DURING_DEPLOYMENT, "true");
assert.equal(env.ENABLE_ORYX_BUILD, "true");
assert.match(env.GATEWAY_PUBLIC_ORIGIN, /defaultHostname/);
assert.equal(env.ENTRA_AUDIENCE, "[parameters('gatewayApiClientId')]");
assert.equal(env.ENTRA_TENANT_ID, "[parameters('entraTenantId')]");
assert.equal(env.ENTRA_REQUIRED_SCOPES, "Charts.Read");
assert.equal(env.FOUNDRY_PROJECT_ENDPOINT, "[parameters('foundryProjectEndpoint')]");
assert.equal(env.FOUNDRY_AGENT_NAME, "[parameters('foundryAgentName')]");
assert.equal(env.FOUNDRY_AGENT_VERSION, "[parameters('foundryAgentVersion')]");
assert.equal(env.GATEWAY_MCP_ORIGINS, "[parameters('mcpOrigins')]");

const vaultDeployment = component("keyVault", "keyVaultName");
const vault = values(vaultDeployment);
assert.equal(vault.sku, "standard");
assert.equal(vault.enableRbacAuthorization, true);
assert.equal(vault.softDeleteRetentionInDays, 7);
assert.equal(unwrap(vaultDeployment).parameters.enablePurgeProtection.defaultValue, true);
const insights = values(component("appInsights", "appInsightsName"));
assert.equal(insights.disableLocalAuth, false);
assert.equal(insights.disableIpMasking, false);

const vaultRoles = unwrap(deployment(root, "kvRoleAssignments"));
assert.equal(resources(vaultRoles).length, 1);
assert.ok(JSON.stringify(vaultRoles).includes("b86a8fe4-44ce-4948-aee5-eccb2c155cd7"));
assert.ok(!JSON.stringify(vaultRoles).includes("4633458b-17de-408a-b874-0445c86b69e6"));
assert.match(resources(vaultRoles)[0].scope, /Microsoft.KeyVault\/vaults/);
const foundryRoles = unwrap(deployment(root, "foundryRoleAssignment"));
assert.equal(resources(foundryRoles).length, 1);
assert.ok(JSON.stringify(foundryRoles).includes("eed3b665-ab3a-47b6-8f48-c9382fb1dad6"));
assert.match(resources(foundryRoles)[0].scope, /Microsoft.CognitiveServices\/accounts\/projects/);
assert.ok(!Object.keys(root.outputs).some((key) => /secret|connection|instrumentation/i.test(key)));

console.log("PASS: compiled gateway configuration, parameter contract, identity, role scopes and safe outputs.");
console.log("This is static validation; Azure quota, policy, permissions and live chart invocation still require verification.");

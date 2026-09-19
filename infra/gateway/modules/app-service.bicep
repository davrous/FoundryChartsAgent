param location string
param tags resourceInput<'Microsoft.Web/sites@2025-03-01'>.tags
param appServiceName string
param appServicePlanId string
param appInsightsResourceId string
param gatewayApiClientId string
param mcpOrigins string
param foundryProjectEndpoint string
param foundryAgentName string
param foundryAgentVersion string
param entraTenantId string

module appService 'br/public:avm/res/web/site:0.24.0' = {
  name: appServiceName
  params: {
    name: appServiceName
    location: location
    tags: tags
    kind: 'app,linux'
    serverFarmResourceId: appServicePlanId
    reserved: true
    managedIdentities: {
      systemAssigned: true
    }
    httpsOnly: true
    publicNetworkAccess: 'Enabled'
    clientAffinityEnabled: false
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.13'
      appCommandLine: 'python -m gateway.main'
      alwaysOn: true
      minTlsVersion: '1.2'
      scmMinTlsVersion: '1.2'
      ftpsState: 'Disabled'
      http20Enabled: true
      healthCheckPath: '/health'
    }
    basicPublishingCredentialsPolicies: [
      { name: 'scm', allow: false }
      { name: 'ftp', allow: false }
    ]
    enableTelemetry: false
  }
}

// Configure after site creation so Azure's actual hostname is available without a dependency cycle.
module appSettings 'br/public:avm/res/web/site/config:0.2.2' = {
  name: '${appServiceName}-settings'
  params: {
    appName: appService.outputs.name
    name: 'appsettings'
    applicationInsightResourceId: appInsightsResourceId
    enableTelemetry: false
    properties: {
      SCM_DO_BUILD_DURING_DEPLOYMENT: 'true'
      ENABLE_ORYX_BUILD: 'true'
      ORYX_DISABLE_COMPRESSION: 'true'
      WEBSITES_CONTAINER_START_TIME_LIMIT: '1800'
      WEBSITE_WARMUP_PATH: '/health'
      ApplicationInsightsAgent_EXTENSION_VERSION: '~3'
      OTEL_SERVICE_NAME: appServiceName
      OTEL_TRACES_SAMPLER_ARG: '0.2'
      OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT: 'false'
      AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED: 'false'
      AZURE_TRACING_GEN_AI_INCLUDE_BINARY_DATA: 'false'
      GATEWAY_MODE: 'foundry'
      GATEWAY_HOST: '0.0.0.0'
      GATEWAY_PORT: '8000'
      GATEWAY_PUBLIC_ORIGIN: 'https://${appService.outputs.defaultHostname}'
      CHARTS_DEV_MODE: 'false'
      FOUNDRY_PROJECT_ENDPOINT: foundryProjectEndpoint
      FOUNDRY_AGENT_NAME: foundryAgentName
      FOUNDRY_AGENT_VERSION: foundryAgentVersion
      ENTRA_TENANT_ID: entraTenantId
      ENTRA_REQUIRED_SCOPES: 'Charts.Read'
      ENTRA_AUDIENCE: gatewayApiClientId
      GATEWAY_MCP_ORIGINS: mcpOrigins
    }
  }
}

output appServiceId string = appService.outputs.resourceId
output appServiceName string = appService.outputs.name
output principalId string = appService.outputs.systemAssignedMIPrincipalId!
output gatewayPublicOrigin string = 'https://${appService.outputs.defaultHostname}'
output mcpServerUrl string = 'https://${appService.outputs.defaultHostname}/mcp'

targetScope = 'subscription'

@minLength(1)
@maxLength(64)
param environmentName string

@minLength(1)
param location string

param sessionId string

param deployedBy string

param createdAt string

param deployerObjectId string

param resourceGroupName string = 'davrousai-rg'

param appServicePlanName string = 'asp-chartsmcp-prod-7c90'

param appServiceName string = 'app-chartsmcp-prod-7c90'

param appInsightsName string = 'appi-chartsmcp-prod-7c90'

param keyVaultName string = 'kv-chartsmcp-prod-7c90'

param existingLogAnalyticsWorkspaceId string = '/subscriptions/480af19d-6138-460b-962c-e2a0b5aca925/resourceGroups/davrousai-rg/providers/Microsoft.OperationalInsights/workspaces/la-foundry-yqmdrsmng3v64'

@minLength(36)
@maxLength(36)
param gatewayApiClientId string

param mcpOrigins string = ''

param foundryAccountName string = 'davrousfoundry'
param foundryProjectName string = 'proj-dav'
param foundryProjectEndpoint string = 'https://davrousfoundry.services.ai.azure.com/api/projects/proj-dav'
param foundryAgentName string = 'charts-agent'
param foundryAgentVersion string = '2'
param entraTenantId string = subscription().tenantId

var tags = {
  'app-onboard-skill': 'true'
  'app-onboard-session-id': sessionId
  'created-at': createdAt
  environment: environmentName
  'deployed-by': deployedBy
}

resource existingRg 'Microsoft.Resources/resourceGroups@2023-07-01' existing = {
  name: resourceGroupName
}

module appServicePlan './modules/app-service-plan.bicep' = {
  name: 'appServicePlan'
  scope: existingRg
  params: {
    location: location
    tags: tags
    appServicePlanName: appServicePlanName
  }
}

module keyVault './modules/key-vault.bicep' = {
  name: 'keyVault'
  scope: existingRg
  params: {
    location: location
    tags: tags
    keyVaultName: keyVaultName
  }
}

module appInsights './modules/app-insights.bicep' = {
  name: 'appInsights'
  scope: existingRg
  params: {
    location: location
    tags: tags
    appInsightsName: appInsightsName
    logAnalyticsWorkspaceId: existingLogAnalyticsWorkspaceId
  }
}

module appService './modules/app-service.bicep' = {
  name: 'appService'
  scope: existingRg
  params: {
    location: location
    tags: tags
    appServiceName: appServiceName
    appServicePlanId: appServicePlan.outputs.appServicePlanId
    appInsightsResourceId: appInsights.outputs.resourceId
    gatewayApiClientId: gatewayApiClientId
    mcpOrigins: mcpOrigins
    foundryProjectEndpoint: foundryProjectEndpoint
    foundryAgentName: foundryAgentName
    foundryAgentVersion: foundryAgentVersion
    entraTenantId: entraTenantId
  }
}

module kvRoleAssignments './modules/kv-role-assignments.bicep' = {
  name: 'kvRoleAssignments'
  scope: existingRg
  params: {
    keyVaultName: keyVault.outputs.vaultName
    deployerObjectId: deployerObjectId
  }
}

module foundryRoleAssignment './modules/foundry-role-assignment.bicep' = {
  name: 'foundryRoleAssignment'
  scope: existingRg
  params: {
    foundryAccountName: foundryAccountName
    foundryProjectName: foundryProjectName
    principalId: appService.outputs.principalId
    principalType: 'ServicePrincipal'
  }
}

output webAppName string = appService.outputs.appServiceName
output webAppResourceId string = appService.outputs.appServiceId
output gatewayPublicOrigin string = appService.outputs.gatewayPublicOrigin
output mcpServerUrl string = appService.outputs.mcpServerUrl
output gatewayPrincipalId string = appService.outputs.principalId
output keyVaultName string = keyVault.outputs.vaultName
output keyVaultUri string = keyVault.outputs.vaultUri
output appInsightsResourceId string = appInsights.outputs.resourceId

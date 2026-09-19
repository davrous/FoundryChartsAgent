param location string
param tags resourceInput<'Microsoft.Insights/components@2020-02-02'>.tags
param appInsightsName string
param logAnalyticsWorkspaceId string

// Python App Service autoinstrumentation requires local ingestion auth; the gateway API does not.
module appInsights 'br/public:avm/res/insights/component:0.8.0' = {
  name: appInsightsName
  params: {
    name: appInsightsName
    location: location
    tags: tags
    kind: 'web'
    applicationType: 'web'
    workspaceResourceId: logAnalyticsWorkspaceId
    retentionInDays: 30
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
    disableLocalAuth: false
    disableIpMasking: false
    enableTelemetry: false
  }
}

output resourceId string = appInsights.outputs.resourceId

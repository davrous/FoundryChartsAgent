param location string
param tags resourceInput<'Microsoft.Web/serverfarms@2025-03-01'>.tags
param appServicePlanName string

module appServicePlan 'br/public:avm/res/web/serverfarm:0.7.0' = {
  name: appServicePlanName
  params: {
    name: appServicePlanName
    location: location
    tags: tags
    skuName: 'B1'
    skuCapacity: 1
    kind: 'linux'
    reserved: true
    zoneRedundant: false
    enableTelemetry: false
  }
}

output appServicePlanId string = appServicePlan.outputs.resourceId

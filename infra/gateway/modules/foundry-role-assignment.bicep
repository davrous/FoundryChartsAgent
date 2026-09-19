param foundryAccountName string

param foundryProjectName string

param principalId string

param principalType string = 'ServicePrincipal'

var foundryAgentConsumerRoleId = 'eed3b665-ab3a-47b6-8f48-c9382fb1dad6'

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}

resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: foundryAccount
  name: foundryProjectName
}

resource foundryRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundryProject.id, principalId, foundryAgentConsumerRoleId)
  scope: foundryProject
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', foundryAgentConsumerRoleId)
    principalId: principalId
    principalType: principalType
  }
}

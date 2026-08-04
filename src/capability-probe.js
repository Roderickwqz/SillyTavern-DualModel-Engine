export function probeHostCapabilities(adapter) {
    const context = adapter.getContext();
    const reasons = [];
    const isGroupChat = Boolean(context.groupId);

    if (isGroupChat) reasons.push('Group chats are not supported');
    if (!adapter.canInjectPrompt) reasons.push('Prompt injection API is unavailable');
    if (!adapter.canPersist) reasons.push('Chat metadata persistence is unavailable');

    let profiles = [];
    try {
        profiles = adapter.listProfiles();
        if (!profiles.length) reasons.push('No supported Recorder connection profile is configured');
    } catch (error) {
        reasons.push(`Connection Manager is unavailable: ${error.message}`);
    }

    return {
        supported: !isGroupChat && adapter.canInjectPrompt && adapter.canPersist,
        isGroupChat,
        profiles,
        toolApiAvailable: adapter.canRegisterTools,
        promptInjectionAvailable: adapter.canInjectPrompt,
        persistenceAvailable: adapter.canPersist,
        reasons,
    };
}

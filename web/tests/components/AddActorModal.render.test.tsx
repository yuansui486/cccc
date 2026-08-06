import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { AddActorModal } from "../../src/components/modals/AddActorModal";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

describe("AddActorModal pending start state", () => {
  it("offers retry and edit actions without offering a duplicate create", () => {
    const noop = () => undefined;
    const html = renderToStaticMarkup(
      <AddActorModal
        isOpen
        isDark={false}
        busy=""
        hasForeman={false}
        developerMode={false}
        runtimes={[{ name: "opencode", display_name: "OpenCode", available: true, recommended_command: "opencode --auto" }]}
        suggestedActorId="opencode-1"
        newActorId="opencode-1"
        setNewActorId={noop}
        newActorRole="peer"
        setNewActorRole={noop}
        newActorUseProfile={false}
        setNewActorUseProfile={noop}
        newActorProfileId=""
        setNewActorProfileId={noop}
        actorProfiles={[]}
        actorProfilesBusy={false}
        newActorRuntime="opencode"
        setNewActorRuntime={noop}
        newActorRunner="pty"
        setNewActorRunner={noop}
        newActorCommand="opencode --auto"
        setNewActorCommand={noop}
        newActorSecretsSetText=""
        setNewActorSecretsSetText={noop}
        newActorCapabilityAutoloadText=""
        setNewActorCapabilityAutoloadText={noop}
        newActorRoleNotes=""
        setNewActorRoleNotes={noop}
        showAdvancedActor={false}
        setShowAdvancedActor={noop}
        addActorError="actorCreatedStartFailed"
        setAddActorError={noop}
        createdActorId="opencode-1"
        canAddActor
        addActorDisabledReason=""
        onAddActor={noop}
        onEditCreatedActor={noop}
        onSaveAsProfile={noop}
        onClose={noop}
        onCancelAndReset={noop}
      />,
    );

    expect(html).toContain("retryStart");
    expect(html).toContain("editCreatedActor");
    expect(html).not.toContain(">addAgent<");
  });
});

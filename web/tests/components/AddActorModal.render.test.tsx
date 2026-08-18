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
    expect(html).toContain("agent");
    expect(html).toContain("nickname");
    expect(html).not.toContain("commandOverrideOptional");
    expect(html).not.toContain(">addAgent<");
  });

  it("keeps a required launch command input for custom runtimes", () => {
    const noop = () => undefined;
    const html = renderToStaticMarkup(
      <AddActorModal
        isOpen
        isDark={false}
        busy=""
        hasForeman={false}
        runtimes={[]}
        suggestedActorId="custom-1"
        newActorId="custom-1"
        setNewActorId={noop}
        newActorRole="peer"
        setNewActorRole={noop}
        newActorUseProfile={false}
        setNewActorUseProfile={noop}
        newActorProfileId=""
        setNewActorProfileId={noop}
        actorProfiles={[]}
        actorProfilesBusy={false}
        newActorRuntime="custom"
        setNewActorRuntime={noop}
        newActorRunner="pty"
        setNewActorRunner={noop}
        newActorCommand="custom-cli"
        setNewActorCommand={noop}
        newActorSecretsSetText=""
        setNewActorSecretsSetText={noop}
        newActorCapabilityAutoloadText=""
        setNewActorCapabilityAutoloadText={noop}
        newActorRoleNotes=""
        setNewActorRoleNotes={noop}
        showAdvancedActor={false}
        setShowAdvancedActor={noop}
        addActorError=""
        setAddActorError={noop}
        createdActorId=""
        canAddActor
        addActorDisabledReason=""
        onAddActor={noop}
        onEditCreatedActor={noop}
        onSaveAsProfile={noop}
        onClose={noop}
        onCancelAndReset={noop}
      />,
    );

    expect(html).toContain(">command<");
    expect(html).toContain('value="custom-cli"');
    expect(html).toContain('placeholder="enterCommand"');
    expect(html).not.toContain("commandOverrideOptional");
  });
});

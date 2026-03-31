import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { buildHelpMarkdown, parseHelpMarkdown, updateActorHelpNote, updatePetHelpNote } from "./helpMarkdown";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const roundtripFixturePath = resolve(__dirname, "../../../tests/fixtures/help_markdown_pet_roundtrip.md");

describe("helpMarkdown pet persona", () => {
  it("parses and rebuilds the pet block without dropping other sections", () => {
    const markdown = `
Shared guidance.

## @role: foreman

Foreman note.

## @role: peer

Peer note.

## @pet

Pet coordinator note.

## @actor: peer-1

Actor note.
`.trim();

    const parsed = parseHelpMarkdown(markdown);
    expect(parsed.common).toBe("Shared guidance.");
    expect(parsed.foreman).toBe("Foreman note.");
    expect(parsed.peer).toBe("Peer note.");
    expect(parsed.pet).toBe("Pet coordinator note.");
    expect(parsed.actorNotes["peer-1"]).toBe("Actor note.");

    const rebuilt = buildHelpMarkdown({
      common: parsed.common,
      foreman: parsed.foreman,
      peer: parsed.peer,
      pet: parsed.pet,
      actorNotes: parsed.actorNotes,
      actorOrder: ["peer-1"],
      extraTaggedBlocks: parsed.extraTaggedBlocks,
    });

    expect(rebuilt).toContain("## @pet");
    expect(rebuilt).toContain("Pet coordinator note.");
    expect(rebuilt).toContain("## @actor: peer-1");
  });

  it("preserves pet persona when actor notes are updated", () => {
    const markdown = `
## @pet

Keep the cat low-noise.

## @actor: peer-1

Old actor note.
`.trim();

    const updated = updateActorHelpNote(markdown, "peer-1", "New actor note.", ["peer-1"]);
    const parsed = parseHelpMarkdown(updated);

    expect(parsed.pet).toBe("Keep the cat low-noise.");
    expect(parsed.actorNotes["peer-1"]).toBe("New actor note.");
  });

  it("treats malformed inline actor note text as body instead of actor id suffix", () => {
    const markdown = `
## @actor: peer-1 first line
second line
`.trim();

    const parsed = parseHelpMarkdown(markdown);

    expect(parsed.actorNotes["peer-1"]).toBe("first line\nsecond line");
  });

  it("updates the pet block without dropping actor notes", () => {
    const markdown = `
## @actor: peer-1

Actor note.

## @pet

Old pet note.
`.trim();

    const updated = updatePetHelpNote(markdown, "New pet note.", ["peer-1"]);
    const parsed = parseHelpMarkdown(updated);

    expect(parsed.pet).toBe("New pet note.");
    expect(parsed.actorNotes["peer-1"]).toBe("Actor note.");
  });

  it("matches the shared roundtrip fixture used by backend tests", () => {
    const markdown = readFileSync(roundtripFixturePath, "utf8");
    const parsed = parseHelpMarkdown(markdown);

    expect(parsed.common).toBe("Shared guidance.");
    expect(parsed.foreman).toBe("Foreman note.");
    expect(parsed.peer).toBe("Peer note.");
    expect(parsed.pet).toBe("Pet note.");
    expect(parsed.actorNotes["peer-1"]).toBe("Actor note.");
    expect(parsed.actorNotes["reviewer-1"]).toBe("Reviewer note.");
    expect(parsed.extraTaggedBlocks).toEqual([
      "## @role: observer\n\nObserver note.",
    ]);

    const rebuilt = buildHelpMarkdown({
      common: parsed.common,
      foreman: parsed.foreman,
      peer: parsed.peer,
      pet: parsed.pet,
      actorNotes: parsed.actorNotes,
      actorOrder: ["peer-1", "reviewer-1"],
      extraTaggedBlocks: parsed.extraTaggedBlocks,
    });

    expect(rebuilt).toBe(markdown);
  });
});

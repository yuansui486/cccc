import { describe, expect, it } from "vitest";
import { isImageAttachment, isSvgAttachment } from "./messageAttachments";

describe("messageAttachments", () => {
  it("recognizes SVG attachments from mime type", () => {
    const attachment = {
      kind: "file",
      path: "state/blobs/sha_demo.svg",
      title: "demo.svg",
      mime_type: "image/svg+xml",
    };
    expect(isImageAttachment(attachment)).toBe(true);
    expect(isSvgAttachment(attachment)).toBe(true);
  });

  it("falls back to kind and extension when mime type is missing", () => {
    const attachment = {
      kind: "image",
      path: "state/blobs/sha_demo.svg",
      title: "demo.svg",
      mime_type: "",
    };
    expect(isImageAttachment(attachment)).toBe(true);
    expect(isSvgAttachment(attachment)).toBe(true);
  });

  it("does not treat generic files as images", () => {
    const attachment = {
      kind: "file",
      path: "state/blobs/sha_demo.txt",
      title: "demo.txt",
      mime_type: "text/plain",
    };
    expect(isImageAttachment(attachment)).toBe(false);
    expect(isSvgAttachment(attachment)).toBe(false);
  });
});

import { useTranslation } from "react-i18next";
import { useWebPetDrag } from "./useWebPetDrag";
import { CatCanvas } from "./CatCanvas";
import type { CatState, PetReaction } from "./types";
import { WEB_PET_BUBBLE_SIZE } from "./constants";

interface WebPetBubbleProps {
  groupId: string;
  stackIndex?: number;
  state: CatState;
  hint: string;
  reaction: PetReaction;
  onPress?: () => void;
}

export function WebPetBubble({
  groupId,
  stackIndex = 0,
  state,
  hint,
  reaction,
  onPress,
}: WebPetBubbleProps) {
  const { t } = useTranslation("webPet");
  const { isDragging, handlers } = useWebPetDrag(groupId, stackIndex, onPress);

  return (
    <div
      {...handlers}
      className={`pointer-events-auto flex items-center justify-center transition-transform ${
        isDragging ? "cursor-grabbing scale-105" : "cursor-grab"
      }`}
      style={{
        width: WEB_PET_BUBBLE_SIZE,
        height: WEB_PET_BUBBLE_SIZE,
        touchAction: "none",
      }}
      role="button"
      tabIndex={0}
      aria-label={
        hint
          ? t("bubbleAriaHint", {
              defaultValue: "Web Pet. {{hint}}",
              hint,
            })
          : t("bubbleAria", { defaultValue: "Web Pet" })
      }
      onKeyDown={(event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        onPress?.();
      }}
    >
      <CatCanvas state={state} hint={hint} reaction={reaction} />
    </div>
  );
}

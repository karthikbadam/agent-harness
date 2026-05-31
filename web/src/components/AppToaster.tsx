import { Portal, Toast, Toaster as ChakraToaster } from "@chakra-ui/react";

import { toaster } from "./toaster";

export function AppToaster() {
  return (
    <Portal>
      <ChakraToaster toaster={toaster} insetInline={{ mdDown: "4" }}>
        {(t) => (
          <Toast.Root width={{ md: "sm" }}>
            <Toast.Indicator />
            <Toast.Title>{t.title}</Toast.Title>
            {t.description && (
              <Toast.Description>{t.description}</Toast.Description>
            )}
            <Toast.CloseTrigger />
          </Toast.Root>
        )}
      </ChakraToaster>
    </Portal>
  );
}

import { ReactNode, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Box, Container, Flex, HStack, Heading, IconButton } from "@chakra-ui/react";
import { LuChevronLeft, LuSettings } from "react-icons/lu";

import { SettingsDrawer } from "./SettingsDrawer";

interface ShellProps {
  title?: ReactNode;
  right?: ReactNode;
  back?: string | true;
  children: ReactNode;
}

export function Shell({ title, right, back, children }: ShellProps) {
  const navigate = useNavigate();
  const [settingsOpen, setSettingsOpen] = useState(false);

  return (
    <Flex direction="column" minH="100dvh" bg="bg">
      <Box
        as="header"
        position="sticky"
        top={0}
        zIndex={10}
        bg="bg"
        borderBottomWidth="1px"
        px={4}
        py={3}
        pt="max(env(safe-area-inset-top), 12px)"
      >
        <Flex justify="space-between" align="center" maxW="container.sm" mx="auto" gap={2}>
          <HStack gap={2} flex="1" minW={0}>
            {back && (
              <IconButton
                aria-label="Back"
                variant="ghost"
                size="sm"
                onClick={() => (back === true ? navigate(-1) : navigate(back))}
              >
                <LuChevronLeft />
              </IconButton>
            )}
            <Heading size="md" truncate>
              {title ?? "agent-harness"}
            </Heading>
          </HStack>
          <HStack gap={1}>
            {right}
            <IconButton
              aria-label="Settings"
              variant="ghost"
              size="sm"
              onClick={() => setSettingsOpen(true)}
            >
              <LuSettings />
            </IconButton>
          </HStack>
        </Flex>
      </Box>
      <Box as="main" flex="1">
        <Container maxW="container.sm" px={4} py={4}>
          {children}
        </Container>
      </Box>
      <SettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </Flex>
  );
}

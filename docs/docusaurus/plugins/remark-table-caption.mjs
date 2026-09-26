// Copyright (c) 2026 Microsoft Corporation. All rights reserved.
// SPDX-License-Identifier: MIT
// @ts-check
import { visit } from 'unist-util-visit';

function normalizeText(value) {
  return typeof value === 'string' ? value.trim() : undefined;
}

function normalizeBoolean(value) {
  if (value === true || value === 'true' || value === '1' || value === 1) {
    return true;
  }

  if (value === false || value === 'false' || value === '0' || value === 0) {
    return false;
  }

  return undefined;
}

export default function remarkTableCaption() {
  return (tree) => {
    visit(tree, (node, index, parent) => {
      if (!parent || typeof index !== 'number') {
        return;
      }

      if (node.type !== 'containerDirective' || node.name !== 'table') {
        return;
      }

      const tableChild = node.children?.find((child) => child.type === 'table');
      if (!tableChild) {
        return;
      }

      const attributes = node.attributes ?? {};
      const captionText = normalizeText(attributes.caption);
      const rowheader = normalizeBoolean(attributes.rowheader);

      tableChild.data ??= {};
      tableChild.data.hProperties ??= {};
      if (captionText) {
        tableChild.data.hProperties['data-caption'] = captionText;
      }
      if (rowheader === true) {
        tableChild.data.hProperties['data-rowheader'] = 'true';
      }

      parent.children[index] = tableChild;
    });
  };
}
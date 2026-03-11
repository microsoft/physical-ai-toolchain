import React from 'react';
import Navbar from '@theme-original/Navbar';
import type { WrapperProps } from '@docusaurus/types';

type Props = WrapperProps<typeof Navbar>;

export default function NavbarWrapper(props: Props): React.ReactElement {
  return (
    <>
      <Navbar {...props} />
    </>
  );
}

import React from 'react';
import Footer from '@theme-original/Footer';
import type { WrapperProps } from '@docusaurus/types';

type Props = WrapperProps<typeof Footer>;

export default function FooterWrapper(props: Props): React.ReactElement {
  return (
    <>
      <Footer {...props} />
    </>
  );
}

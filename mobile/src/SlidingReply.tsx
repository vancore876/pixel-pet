import React, { useEffect, useRef, useState } from 'react';
import { Animated, Platform, StyleSheet, Text, View } from 'react-native';
export function SlidingReply({ text }: { text: string }) {
  const [lines, setLines] = useState(text.split('\n'));
  const signature = useRef(text);
  useEffect(() => { if (signature.current !== text) { signature.current = text; setLines(text.split('\n')); } }, [text]);
  return <View>
    {Platform.OS !== 'web' && <Text style={[styles.text, styles.measure]} onTextLayout={event => {
      const measured = event.nativeEvent.lines.map(line => line.text);
      if (measured.join('\n') !== lines.join('\n')) setLines(measured);
    }}>{text}</Text>}
    {lines.map((line, index) => <SlideLine key={`${text}:${index}:${line}`} text={line} index={index} />)}
  </View>;
}
function SlideLine({ text, index }: { text: string; index: number }) {
  const progress = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    const animation = Animated.timing(progress, { toValue: 1, duration: 220, delay: index * 170, useNativeDriver: true });
    animation.start(); return () => animation.stop();
  }, [index, progress]);
  return <Animated.Text style={[styles.text, { opacity: progress, transform: [{ translateY: progress.interpolate({ inputRange: [0, 1], outputRange: [-9, 0] }) }] }]}>{text || ' '}</Animated.Text>;
}
const styles = StyleSheet.create({ text: { fontSize: 15, lineHeight: 23, color: '#e4edf6' }, measure: { position: 'absolute', opacity: 0, left: 0, right: 0 } });

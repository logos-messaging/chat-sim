//! The numbers a member's frames are drawn against.

/// SplitMix64: Steele, Lea, Flood, "Fast splittable pseudorandom number
/// generators", OOPSLA 2014, as `splitmix64.c` by Vigna writes it.
#[derive(Debug, Clone)]
pub(crate) struct SplitMix64(u64);

impl SplitMix64 {
    /// The stream of `member` under `seed`. A name gives the same stream
    /// whoever else has one, so adding a member moves no other's.
    pub(crate) fn of_member(seed: u64, member: &str) -> Self {
        let seed = seed.to_le_bytes();
        Self(fnv1a(seed.iter().chain(member.as_bytes())))
    }

    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    /// The next number, from 0 up to and not 1: the top 53 bits of an output.
    pub(crate) fn next_unit(&mut self) -> f64 {
        const TWO_TO_MINUS_53: f64 = 1.0 / (1u64 << 53) as f64;
        (self.next() >> 11) as f64 * TWO_TO_MINUS_53
    }
}

/// FNV-1a, 64 bits.
fn fnv1a<'a>(bytes: impl IntoIterator<Item = &'a u8>) -> u64 {
    bytes.into_iter().fold(0xcbf2_9ce4_8422_2325, |hash, byte| {
        (hash ^ u64::from(*byte)).wrapping_mul(0x0100_0000_01b3)
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn outputs<const N: usize>(mut stream: SplitMix64) -> [u64; N] {
        std::array::from_fn(|_| stream.next())
    }

    #[test]
    fn splitmix64_gives_its_published_outputs() {
        assert_eq!(
            outputs(SplitMix64(0)),
            [
                0xE220_A839_7B1D_CDAF,
                0x6E78_9E6A_A1B9_65F4,
                0x06C4_5D18_8009_454F
            ]
        );
        assert_eq!(
            outputs(SplitMix64(1_234_567)),
            [
                6_457_827_717_110_365_317,
                3_203_168_211_198_807_973,
                9_817_491_932_198_370_423,
                4_593_380_528_125_082_431,
                16_408_922_859_458_223_821
            ]
        );
    }

    #[test]
    fn fnv1a_gives_its_published_hashes() {
        assert_eq!(fnv1a(b""), 0xcbf2_9ce4_8422_2325);
        assert_eq!(fnv1a(b"a"), 0xaf63_dc4c_8601_ec8c);
        assert_eq!(fnv1a(b"foobar"), 0x8594_4171_f739_67e8);
    }

    #[test]
    fn a_members_stream_starts_at_the_hash_of_the_seed_and_its_name() {
        assert_eq!(SplitMix64::of_member(7, "saro").0, 0x8CFE_0753_E8D0_1C49);
        assert_eq!(
            outputs(SplitMix64::of_member(7, "saro")),
            [
                0x18FB_C273_C4DE_2CEE,
                0x4333_163D_6128_27C6,
                0x202E_2DB4_483F_A551
            ]
        );
        assert_eq!(SplitMix64::of_member(0, "").0, 0xA8C7_F832_281A_39C5);
    }

    #[test]
    fn a_number_is_the_top_53_bits_of_an_output_over_two_to_the_53() {
        let mut stream = SplitMix64::of_member(7, "saro");
        assert_eq!(stream.next_unit(), 0.09759154631034817);
        assert_eq!(stream.next_unit(), 0.26249827383540536);

        let mut stream = SplitMix64(0);
        for _ in 0..10_000 {
            assert!((0.0..1.0).contains(&stream.next_unit()));
        }
    }
}

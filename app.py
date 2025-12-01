import streamlit as st
import pandas as pd

from player_stats import (
    scrape_player_game_log,
    add_parsed_columns,
    add_game_advanced_metrics,
    summarize_overall,
    summarize_splits,
    build_summary_html,
    find_usbasket_player_url_by_name, 
)


# ============================================================
# Streamlit app
# ============================================================

st.set_page_config(page_title="Player Advanced Stats", layout="wide")
st.title("Basketball Player Advanced Stats")

st.markdown(
    "Paste a **basketball.usbasket.com** player URL below, **or** select "
    "`Player name` and type the name. "
    "The app will scrape the **latest-season game log**, compute advanced metrics, "
    "and let you download both a CSV and a nicely formatted HTML summary."
)

input_mode = st.radio("How do you want to select the player?", ["Player URL", "Player name"])

default_url = "https://basketball.usbasket.com/player/LeBron-James/52424"

if input_mode == "Player URL":
    url = st.text_input("Player URL", value=default_url)
    player_name = ""
else:
    player_name = st.text_input("Player name", value="")
    url = ""

if st.button("Analyze player"):
    final_url = None

    if input_mode == "Player URL":
        final_url = url.strip()
    else:
        name = player_name.strip()
        if not name:
            st.error("Please enter a player name.")
            st.stop()

        with st.spinner("Looking up USBasket player URL for that name..."):
            found = find_usbasket_player_url_by_name(name)

        if found:
            st.success(f"Found player page: {found}")
            final_url = found
        else:
            st.error(
                "Couldn't automatically find a basketball.usbasket.com player page "
                "for that name. Try a different player or paste the exact player URL instead."
            )
            st.stop()

    if not final_url:
        st.error("Please enter a valid USBasket player URL.")
        st.stop()

    # ---------- existing analysis logic, but using final_url ----------
    try:
        with st.spinner("Scraping and analyzing data..."):
            raw_df = scrape_player_game_log(final_url)

            st.subheader("Raw scraped game log (latest season)")
            st.dataframe(raw_df, use_container_width=True)

            df = raw_df.copy()
            df = add_parsed_columns(df)
            df = add_game_advanced_metrics(df)

            st.subheader("Game log with advanced metrics")
            cols_to_show = [
                "Date",
                "Team",
                "Against Team",
                "Result",
                "MIN",
                "PTS",
                "reb",
                "AS",
                "ST",
                "BS",
                "two_made",
                "two_att",
                "three_made",
                "three_att",
                "ft_made",
                "ft_att",
                "efg",
                "ts",
                "game_score",
                "pts_per_36",
                "fga_per_36",
                "margin",
                "win",
            ]
            cols_to_show = [c for c in cols_to_show if c in df.columns]
            st.dataframe(df[cols_to_show], use_container_width=True)

            overall = summarize_overall(df)
            splits = summarize_splits(df)

            st.subheader("Overall summary")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Games", overall["games"])
            c2.metric("Total minutes", f"{overall['total_minutes']:.1f}")
            c3.metric("Season eFG%", f"{overall['efg']:.3f}")
            c4.metric("Season TS%", f"{overall['ts']:.3f}")

            st.markdown("**Per-game averages**")
            st.dataframe(
                pd.DataFrame(overall["per_game"], index=["Per game"]).T,
                use_container_width=True,
            )

            st.markdown("**Per-36-minute averages**")
            st.dataframe(
                pd.DataFrame(overall["per_36"], index=["Per 36"]).T,
                use_container_width=True,
            )

            st.markdown("**Totals**")
            st.dataframe(
                pd.DataFrame(overall["totals"], index=["Total"]).T,
                use_container_width=True,
            )

            st.markdown("**Shooting totals**")
            st.dataframe(
                pd.DataFrame(overall["shooting_totals"], index=["Total"]).T,
                use_container_width=True,
            )

            st.markdown(f"**Average Game Score:** {overall['avg_game_score']:.2f}")

            st.subheader("Splits")
            st.markdown("**Wins vs. losses**")
            win_loss_df = splits["wins_vs_losses"].copy().round(2)
            win_loss_df.index = win_loss_df.index.map(lambda x: "Win" if x else "Loss")
            st.dataframe(win_loss_df, use_container_width=True)

            st.markdown("**By opponent**")
            st.dataframe(splits["by_opponent"].round(2), use_container_width=True)

            st.markdown("**Correlations**")
            st.write(
                f"- Corr(MIN, PTS): `{splits['corr_MIN_PTS']:.3f}`  \n"
                f"- Corr(MIN, Game Score): `{splits['corr_MIN_GameScore']:.3f}`"
            )

            summary_html = build_summary_html(final_url, overall, splits)
            summary_bytes = summary_html.encode("utf-8")

            st.download_button(
                label="📥 Download summary (HTML)",
                data=summary_bytes,
                file_name="player_summary.html",
                mime="text/html",
            )

            csv_bytes = df.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="📥 Download game log + advanced metrics (CSV)",
                data=csv_bytes,
                file_name="player_game_log_advanced.csv",
                mime="text/csv",
            )

    except Exception as e:
        st.error(f"Something went wrong: {e}")

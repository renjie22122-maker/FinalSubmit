package org.twak.viewTrace.franken.style.ui;

import java.awt.BorderLayout;
import java.awt.event.ActionEvent;
import java.awt.event.ActionListener;
import java.awt.image.BufferedImage;
import java.io.File;
import java.io.IOException;

import javax.swing.JButton;
import javax.swing.JLabel;
import javax.swing.JOptionPane;
import javax.swing.JPanel;
import javax.swing.SwingUtilities;
import javax.swing.JToggleButton;

import org.twak.utils.ui.ListDownLayout;
import org.twak.viewTrace.franken.NetInfo;
import org.twak.viewTrace.franken.Pix2Pix;
import org.twak.viewTrace.franken.Pix2Pix.ReferenceEncodeResult;
import org.twak.viewTrace.franken.RoofTexApp;
import org.twak.viewTrace.franken.style.ReferenceStyleSupport;

public class UIVector extends JPanel {

	public static double[] copiedVector;
	
	double[] vector;
	JToggleButton method ;
	MeanImageProvider imageFile;
	Class target;
	File defaultReference;
	
	public UIVector( double[] vector, MeanImageProvider imageFile, Class target, boolean showManual, Runnable update ) {
		this( vector, imageFile, target, showManual, update, null );
	}

	public UIVector( double[] vector, MeanImageProvider imageFile, Class target, boolean showManual,
			Runnable update, File defaultReference ) {

		this.target = target;
		this.vector = vector;
		this.imageFile = imageFile;
		this.defaultReference = defaultReference;
		
		setLayout( new BorderLayout() );
		
		method = new JToggleButton("manual");

		method.setSelected( false );//imageFile.getMeanImage() == null );
		
		JPanel options = new JPanel(new ListDownLayout());
		
		method.addActionListener( new ActionListener() {
			@Override
			public void actionPerformed( ActionEvent e ) {
				setUI (options, !method.isSelected(), update );
			}
		} );
		
		if (showManual)
			add (method, BorderLayout.NORTH );
		
		add (options, BorderLayout.CENTER );
		
		setUI (options, !method.isSelected(), update );
	}
	
	public void setUI (JPanel out, boolean byExample, Runnable update) {
		
		
		out.removeAll();

		
		if ( byExample ) {
			
			boolean supportedReference = ReferenceStyleSupport.supportsReferenceImage( target );
			ImageFileDrop drop = new ImageFileDrop( imageFile.getMeanImage(),
					ReferenceStyleSupport.loadPrompt( target ) ) {
				
				public BufferedImage process( File f ) throws Throwable {
					ReferenceEncodeResult result = new Pix2Pix( NetInfo.index.get( target ) )
							.encodeStyleReference( f, vector.length );
					if ( !result.succeeded() )
						throw new IOException( result.error );

					runOnEdtAndWait( () -> {
						if ( imageFile instanceof ReferenceVectorProvider )
							( (ReferenceVectorProvider) imageFile ).applyReferenceVector(
									result.latent, result.preview );
						else {
							ReferenceStyleSupport.commitLatent( vector, result.latent );
							imageFile.setMeanImage( null );
						}
						update.run();
					} );
					return result.preview;
				};

				@Override
				protected void failed( Throwable error ) {
					SwingUtilities.invokeLater( () -> JOptionPane.showMessageDialog( UIVector.this,
							error.getMessage(), "Reference style was not changed",
							JOptionPane.ERROR_MESSAGE ) );
				}
				
				@Override
				public void rightClick() {
					super.rightClick();
					method.doClick();
				}
			};

			if ( supportedReference ) {
				JPanel referenceActions = new JPanel( new BorderLayout() );
				String unavailable = ReferenceStyleSupport.encoderUnavailableReason( NetInfo.index.get( target ) );
				JLabel capability = new JLabel( unavailable == null ? "FrankenGAN encoder ready"
						: "Start FrankenGAN watcher before loading" );
				capability.setToolTipText( unavailable );
				referenceActions.add( capability, BorderLayout.CENTER );

				JButton clear = new JButton( "Clear / use random" );
				clear.addActionListener( event -> {
					if ( imageFile instanceof ReferenceVectorProvider )
						( (ReferenceVectorProvider) imageFile ).clearReferenceVector();
					else
						ReferenceStyleSupport.commitLatent( vector, new double[ vector.length ] );
					drop.clearImage();
					update.run();
				} );
				referenceActions.add( clear, BorderLayout.EAST );
				out.add( referenceActions );

				if ( target == RoofTexApp.class ) {
					JButton usePublished = new JButton( "Use satellite roof reference" );
					usePublished.setEnabled( defaultReference != null );
					usePublished.setToolTipText( defaultReference == null
							? "Select exactly one block with a READY satellite roof reference."
							: defaultReference.getPath() );
					usePublished.addActionListener( event -> drop.loadFile( defaultReference ) );
					out.add( usePublished );
				}
			}
			out.add( drop );
		} else {

//			imageFile.setMeanImage( null );
			
			NSliders sliders = new NSliders( vector, update, new Runnable() {
				@Override
				public void run() {
					method.doClick();
				}
				@Override
				public String toString() {
					return "from image";
				}
			} );
			out.add( sliders );
		}
		
		out.repaint();
		out.revalidate();
	}

	private static void runOnEdtAndWait( Runnable runnable ) throws Exception {
		if ( SwingUtilities.isEventDispatchThread() )
			runnable.run();
		else
			SwingUtilities.invokeAndWait( runnable );
	}
	
	public interface MeanImageProvider {
		public BufferedImage getMeanImage();
		public void setMeanImage (File f);
	}

	public interface ReferenceVectorProvider extends MeanImageProvider {
		public void applyReferenceVector( double[] encoded, BufferedImage preview );
		public void clearReferenceVector();
		public boolean hasReferenceVector();
	}
}
